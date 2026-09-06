"""Erzwingt die Schichtdisziplin aus doc/ARCHITECTURE.md.

Diese Regeln sind der Kern der Spezifikationen 09 und 10. Ohne automatische
Pruefung erodieren sie unbemerkt: ein einzelner bequemer Import aus der GUI in
ein Repository faellt in der Codepruefung durch und macht die Schicht wertlos.

Geprueft wird der Quelltext, nicht die Laufzeit - ein verbotener Import faellt
damit auf, auch wenn der betroffene Zweig nie ausgefuehrt wird.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# Paket -> Praefixe, die es NICHT importieren darf
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "core": ("PyQt6", "PySide6", "app.services", "app.gui", "app.repositories",
             "app.jobs", "app.analysis", "app.filesystem", "app.database"),
    "analysis": ("PyQt6", "PySide6", "app.gui", "app.services", "app.jobs"),
    "repositories": ("PyQt6", "PySide6", "app.gui", "app.services", "app.jobs"),
    "filesystem": ("PyQt6", "PySide6", "app.gui", "app.services", "app.jobs"),
    "cache": ("PyQt6", "PySide6", "app.gui", "app.services"),
    "database": ("PyQt6", "PySide6", "app.gui", "app.services", "app.jobs"),
    "dto": ("PyQt6", "PySide6", "app.gui", "app.services", "app.repositories"),
    "jobs": ("app.gui",),
    "gui": ("sqlite3", "app.repositories", "app.database"),
}


def _imports_of(path: pathlib.Path) -> list[tuple[str, int]]:
    """Alle importierten Modulnamen einer Datei mit Zeilennummer."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, node.lineno))
    return found


def _python_files(package: str) -> list[pathlib.Path]:
    return sorted((APP / package).rglob("*.py"))


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_layer_does_not_import_upwards(package: str) -> None:
    forbidden = FORBIDDEN[package]
    violations: list[str] = []

    for path in _python_files(package):
        for module, line in _imports_of(path):
            for prefix in forbidden:
                if module == prefix or module.startswith(prefix + "."):
                    violations.append(
                        str(path.relative_to(APP.parent)) + ":" + str(line) + " -> " + module
                    )

    assert not violations, (
        "app/" + package + " verletzt die Datenflussrichtung:\n  " + "\n  ".join(violations)
    )


def test_gui_talks_only_to_services_and_dtos() -> None:
    """Spezifikation 10: die Oberflaeche kennt nur Services und DTOs.

    Ausnahmen sind die Querschnittsschicht core, die Jobhuellen und die
    Composition Root, die die GUI mit fertigen Objekten versorgt.
    """
    allowed = ("app.services", "app.dto", "app.core", "app.gui", "app.jobs", "app.application", "app")
    violations: list[str] = []

    for path in _python_files("gui"):
        for module, line in _imports_of(path):
            if not module.startswith("app"):
                continue
            if not any(module == a or module.startswith(a + ".") for a in allowed):
                violations.append(
                    str(path.relative_to(APP.parent)) + ":" + str(line) + " -> " + module
                )

    assert not violations, "GUI greift an der Service-Schicht vorbei:\n  " + "\n  ".join(violations)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Die Konstanten, die Docstrings sind - sie zaehlen nicht als Code."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    found.add(id(body[0].value))
    return found


def test_sql_lives_only_in_repositories_and_database() -> None:
    """Spezifikation 09: alle SQL-Abfragen ausschliesslich in der Repository-Schicht.

    Geprueft werden Zeichenkettenliterale im Code, nicht der Rohtext. Ein
    Docstring, der das Wort SELECT erwaehnt, ist keine Abfrage.
    """
    keywords = ("SELECT ", "INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE", "PRAGMA ")
    allowed_packages = {"repositories", "database"}
    violations: list[str] = []

    for package in sorted(p.name for p in APP.iterdir() if p.is_dir()):
        if package in allowed_packages or package.startswith("__"):
            continue
        for path in _python_files(package):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstrings = _docstring_nodes(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docstrings:
                    continue
                if any(keyword in node.value.upper() for keyword in keywords):
                    violations.append(
                        str(path.relative_to(APP.parent))
                        + ":" + str(node.lineno) + " " + node.value.strip()[:70]
                    )

    assert not violations, "SQL ausserhalb der Repository-Schicht:\n  " + "\n  ".join(violations)


def test_filesystem_access_is_confined() -> None:
    """Datei- und NAS-Zugriffe nur in app/filesystem, plus begruendete Ausnahmen.

    Die Ausnahmen sind alle vom Typ "wird gebraucht, bevor es eine Schicht gibt"
    oder "ist selbst ein Primitiv":

      config/bootstrap.py   liest die Konfiguration, bevor die Datenbank offen ist
      core/logging_setup.py oeffnet die Logdatei
      core/text.py          die gemeinsame Dekodierhilfe
      application.py        legt beim Start Verzeichnisse an
      tools/gen_testlib.py  Werkzeug, nicht Teil der Anwendung
    """
    allowed_dirs = {APP / "filesystem"}
    allowed_files = {
        APP / "config" / "bootstrap.py",
        APP / "core" / "logging_setup.py",
        APP / "core" / "text.py",
        APP / "application.py",
    }
    # Qualifizierte Namen, damit str.replace, list.remove und Path.mkdir nicht
    # faelschlich als Dateizugriff gelten. self._files.read_bytes(...) ist die
    # erlaubte Delegation an die Dateisystemschicht und hat keinen dieser Namen.
    forbidden = {
        "open",
        "os.scandir", "os.walk", "os.listdir", "os.startfile", "os.remove",
        "os.rename", "os.replace", "os.rmdir", "os.utime", "os.makedirs",
        "subprocess.Popen", "subprocess.run", "subprocess.call",
        "shutil.copy", "shutil.copy2", "shutil.move", "shutil.rmtree",
        "Image.open",
    }
    violations: list[str] = []

    def qualified(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = qualified(node.value)
            return base + "." + node.attr if base else None
        return None

    for path in sorted(APP.rglob("*.py")):
        if path in allowed_files or any(d in path.parents for d in allowed_dirs):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = qualified(node.func)
            if name in forbidden:
                violations.append(
                    str(path.relative_to(APP.parent)) + ":" + str(node.lineno) + " " + name + "()"
                )

    assert not violations, "Dateizugriff ausserhalb der Dateisystemschicht:\n  " + "\n  ".join(
        violations
    )


def test_every_package_documents_its_contract() -> None:
    """Jedes Paket traegt seinen Schichtvertrag im __init__.py.

    Die leeren Pakete spaeterer Meilensteine sind damit nicht nur Platzhalter,
    sondern halten fest, was dort hineingehoert und was nicht.
    """
    missing: list[str] = []
    for init in sorted(APP.rglob("__init__.py")):
        tree = ast.parse(init.read_text(encoding="utf-8"))
        if not ast.get_docstring(tree):
            missing.append(str(init.relative_to(APP.parent)))
    assert not missing, "Paket ohne Schichtvertrag:\n  " + "\n  ".join(missing)


class TestJederAbbruchIstErreichbar:
    """Ein Abbruch, den niemand ausloesen kann, ist keiner.

    Gefunden beim Durchsehen: `AnalysisService.cancel_analysis` und
    `EvolutionService.cancel_build` gab es seit M4 bzw. M6, im Menue stand
    aber nur "Analyse abbrechen" - und das traf allein das Zerlegen.
    Ausgerechnet die beiden Laeufe, die auf einer grossen Bibliothek Minuten
    dauern, liessen sich nicht anhalten.

    Diese Pruefung ist bewusst am Quelltext und nicht am gebauten Fenster:
    ein MainWindow braucht eine eingelesene Bibliothek, und die Frage ist eine
    der Verdrahtung, nicht des Verhaltens.
    """

    def test_alle_abbruchmethoden_haengen_am_menue(self) -> None:
        import re

        fenster = (APP / "gui" / "main_window.py").read_text(encoding="utf-8")

        angeboten: list[tuple[str, str]] = []
        for name in ("analysis_service", "evolution_service", "index_service"):
            quelle = (APP / "services" / (name + ".py")).read_text(encoding="utf-8")
            for treffer in re.findall(r"def (cancel_\w+)\(", quelle):
                angeboten.append((name, treffer))

        assert angeboten, "keine Abbruchmethode gefunden - Pruefung waere wirkungslos"
        fehlend = [
            name + "." + methode
            for name, methode in angeboten
            if methode not in fenster
        ]
        assert not fehlend, (
            "Diese Abbrueche bietet ein Dienst an, aber das Hauptfenster ruft"
            " sie nirgends: " + ", ".join(fehlend)
        )


#: Einstellungen, die es gibt, die aber niemand liest. Sie stehen im
#: Einstellungsdialog und tun nichts - das ist schlimmer als ein fehlender
#: Regler, weil der Benutzer sich auf die Wirkung verlaesst.
#:
#: Die Liste ist eine SCHULD, kein Ausnahmenverzeichnis. Wer eine Einstellung
#: anschliesst, streicht sie hier. Wer eine neue hinzufuegt, ohne sie zu lesen,
#: faellt auf. Drei davon sind baulich nicht erfuellbar - sie werden gebraucht,
#: BEVOR die Datenbank offen ist, in der sie stehen; sie gehoeren in die
#: bootstrap.json oder gestrichen.
UNGELESENE_EINSTELLUNGEN = {
    "library.follow_subfolders",
    "index.hash_images",
    "index.hash_partial_bytes",
    "index.auto_reindex_on_schema_change",   # baulich nicht erfuellbar
    "thumbnails.max_cache_mb",
    "thumbnails.preload_rows",
    "cache.ram_prompts",
    "cache.cleanup_interval_s",
    "search.fts_prefix_matching",
    "analysis.evolution_auto_rebuild",
    "parsing.index_words",
    "ui.restore_session",
    "system.log_level",                      # baulich nicht erfuellbar
    "system.debug_mode",
    "system.backup_on_migration",            # baulich nicht erfuellbar
}


class TestKeineNeuenReglerOhneWirkung:
    """Jede Einstellung wird irgendwo gelesen - oder steht auf der Schuldliste.

    Gefunden beim Durchsehen am 06.09.2026: siebzehn der neunundsechzig
    Einstellungen wurden nirgends gelesen. Zwei liessen sich sofort
    anschliessen (`jobs.db_write_batch`, `system.error_log_max_rows`), weil der
    Baustein dahinter schon existierte. Die uebrigen fuenfzehn stehen oben.
    """

    def _ungelesene(self) -> set[str]:
        import re
        import sys

        sys.path.insert(0, str(APP.parent))
        from app.config.schema import SETTINGS

        schema = APP / "config" / "schema.py"
        gesamt = "\n".join(
            p.read_text(encoding="utf-8")
            for p in APP.rglob("*.py")
            if p != schema
        )
        return {k for k in SETTINGS if not re.search(re.escape(k), gesamt)}

    def test_keine_unbekannte_einstellung_ohne_wirkung(self) -> None:
        neu = self._ungelesene() - UNGELESENE_EINSTELLUNGEN
        assert not neu, (
            "Diese Einstellungen stehen im Dialog, werden aber nirgends"
            " gelesen: " + ", ".join(sorted(neu))
        )

    def test_die_schuldliste_ist_nicht_veraltet(self) -> None:
        """Angeschlossene Einstellungen muessen von der Liste verschwinden."""
        erledigt = UNGELESENE_EINSTELLUNGEN - self._ungelesene()
        assert not erledigt, (
            "Diese Einstellungen werden inzwischen gelesen und gehoeren aus"
            " UNGELESENE_EINSTELLUNGEN gestrichen: " + ", ".join(sorted(erledigt))
        )
