"""Analyse-Schicht: fachliche Intelligenz ohne jede GUI-Kenntnis.

parsing/    Normalisierung, Formaterkennung, Segmentierung
stats.py    doc_count / total_count Pflege, idf / commonness
similarity.py  Kandidatengenerierung + gewichtete Jaccard
clustering.py  Union-Find ueber kNN-Kanten
variants.py    Basisprompt- und Variantenbestimmung
evolution.py   gerichteter Evolutionsbaum (Zeitachse aus mtime)
diff.py        segmentweiser Vergleich zweier Prompts (Uebereinstimmungsquote)
textdiff/      zeilenweiser Vergleich fuer die Vergleichsansicht

Zwei Vergleiche nebeneinander, und das ist Absicht:

`diff.py` arbeitet auf SEGMENTEN aus der Datenbank und beantwortet "wie sehr
aehneln sich diese beiden Prompts". Seine Kennzahl treibt Familien, Varianten
und die Uebereinstimmungsquote.

`textdiff/` arbeitet auf ZEILEN und beantwortet "welche Zeile gehoert neben
welche". Nur daraus entsteht die Darstellung.

Der Vorgaenger hat beides vermischt: die Segmentspannen wurden auf eine
zeilenweise Ausrichtung umgerechnet, zweimal hintereinander. Jede Aenderung an
der einen Seite verschob die andere. Getrennt gehalten kann das nicht mehr
passieren - `textdiff` liefert Positionen relativ zum Zeilenanfang, die keine
Umrechnung mehr brauchen.
"""
