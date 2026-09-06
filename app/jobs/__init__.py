"""Background-Jobs (Querschnittsschicht).

Jobs sprechen ausschliesslich mit Services und Analyse-Engines, niemals mit Widgets.
Getrennte Pools (interactive / io / cpu) plus ein dedizierter DB-Writer-Thread,
siehe doc/ARCHITECTURE.md, Abschnitt "Nebenlaeufigkeit".
"""
