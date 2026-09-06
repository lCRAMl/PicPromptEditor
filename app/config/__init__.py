"""Konfiguration.

Zweistufig (siehe doc/ARCHITECTURE.md, Abschnitt "Konfiguration"):
  * bootstrap.py        - winzige JSON-Datei mit db_path / cache_dir / log_level.
                          Wird gebraucht, BEVOR die Datenbank offen ist.
  * schema.py           - typisierte Registry aller uebrigen Einstellungen.
  * settings_service.py - der einzige erlaubte Zugriffsweg auf Einstellungen.
"""
