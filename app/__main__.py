"""Einstiegspunkt: python -m app

Startet die vollstaendige Anwendung. Mit --check laeuft nur die Startsequenz und
gibt den Systemzustand aus, ohne Oberflaeche - nuetzlich zur Diagnose und in
Umgebungen ohne Bildschirm.
"""

from __future__ import annotations

import argparse
import json
import sys

from app import APP_NAME, __version__
from app.application import Application


def _print_report(report: dict, duration: float, schema: int, notes: list[str]) -> None:
    print(APP_NAME + " " + __version__)
    print("  Start          : " + format(duration, ".3f") + " s")
    print("  Schema         : v" + str(schema))
    print("  Datenbank      : " + str(report.get("db_path", "?")))
    print("  Thumbnail-Cache: " + str(report.get("thumbnail_cache_dir", "?")))
    print("  Logdatei       : " + str(report.get("log_file", "?")))
    print("  Zaehler        : " + str(report.get("counters", {})))
    # Bei einer gepackten Anwendung ist das die einzige Stelle, an der ein
    # unvollstaendiges Paket vor dem ersten Blick in die Oberflaeche auffaellt.
    from app.gui.resources import base_dir, missing_assets

    fehlend = missing_assets()
    print(
        "  Ressourcen     : "
        + ("vollstaendig" if not fehlend else "FEHLEN: " + ", ".join(fehlend))
        + "  (" + str(base_dir()) + ")"
    )
    for note in notes:
        print("  Hinweis        : " + note)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app", description=APP_NAME)
    parser.add_argument("--version", action="version", version=APP_NAME + " " + __version__)
    parser.add_argument(
        "--bootstrap", metavar="PFAD", default=None,
        help="Alternative Bootstrap-Konfigurationsdatei (fuer Tests oder Zweitinstanz)",
    )
    parser.add_argument(
        "--check", action="store_true", help="Nur Startsequenz pruefen, keine Oberflaeche"
    )
    parser.add_argument("--json", action="store_true", help="Statusbericht als JSON")
    args = parser.parse_args(argv)

    application = Application(bootstrap_file=args.bootstrap)
    result = application.startup()

    if not result.ok:
        print("Start fehlgeschlagen: " + result.message, file=sys.stderr)
        if result.recovery_required:
            print(
                "Wiederherstellung erforderlich. Ab M7 bietet die Oberflaeche hier"
                " Reparatur oder Neuindizierung an.",
                file=sys.stderr,
            )
        application.shutdown()
        return 2

    if args.check or args.json:
        report = application.status_report()
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        else:
            _print_report(report, result.duration, result.schema_version, result.notes)
        application.shutdown()
        return 0

    return _run_gui(application)


def _run_gui(application: Application) -> int:
    from PyQt6.QtWidgets import QApplication

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setApplicationVersion(__version__)
    qt_app.setOrganizationName(APP_NAME)

    # Vor dem Hauptfenster: die Widgets sollen gleich in der richtigen Farbe
    # entstehen. Wird der Stil erst danach gesetzt, blitzt das Fenster kurz
    # hell auf, und einzelne Widgets behalten ihre beim Bau gelesenen Farben.
    from app.gui.theme import apply_application_style

    # settings mitgeben: sonst entsteht das Fenster in den Standardfarben des
    # Schemas, und die eingestellte Hintergrund- oder Akzentfarbe erscheint
    # erst, nachdem der Einstellungsdialog einmal mit OK geschlossen wurde.
    apply_application_style(
        application.settings.get_str("ui.theme"), settings=application.settings
    )

    application.build_services()

    from app.gui.main_window import MainWindow

    window = MainWindow(application)
    window.show()

    try:
        return qt_app.exec()
    finally:
        application.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
