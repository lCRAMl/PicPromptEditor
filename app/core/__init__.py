"""Querschnitts-Primitive: Fehlerobjekte, Abbruchsteuerung, Pfade, Logging, Textdekodierung.

Diese Schicht ist absichtlich frei von Qt und von jeder anderen Projektschicht,
damit Analyse-, Repository- und Dateisystemcode ohne QApplication testbar bleibt.
Die Anbindung an Qt-Signale erfolgt erst in app/gui/status/.
"""
