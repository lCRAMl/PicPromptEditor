"""Repository-Schicht - der einzige Ort, an dem SQL vorkommt.

Repositories geben DTOs oder primitive Werte zurueck, niemals sqlite3.Row nach aussen.
Sie kennen keine GUI-Objekte und keine Background-Jobs.
"""
