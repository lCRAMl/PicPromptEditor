# PicPromptEditor

Desktop-Werkzeug für Prompt-Engineering und die Verwaltung großer Bild-/Prompt-Bibliotheken
auf einem NAS. Python 3.13, PyQt6, SQLite im WAL-Modus mit FTS5.

Neben jedem Bild liegt eine Textdatei mit dem Prompt, der es erzeugt hat. Ab einigen
tausend Paaren ist diese Ablage nicht mehr überschaubar: man weiß, dass man diese eine
Formulierung schon einmal benutzt hat, findet aber weder die Datei noch die Fassung, die
funktioniert hat. Genau das löst dieses Programm — es liest die Bibliothek ein, zerlegt die
Prompts in Sätze, findet wiederkehrende Bausteine, gruppiert ähnliche Prompts zu Familien
und macht sichtbar, wie eine Familie sich über die Zeit entwickelt hat.

Entwickelt und kalibriert an einer echten Bibliothek mit rund 28.000 Bildern und
7.000 Prompts auf einer SMB-Freigabe.

---

## Was es kann

| | |
|---|---|
| **Einlesen** | Inkrementeller Abgleich über Größe und Zeitstempel statt Volldurchlauf — ein Rescan ohne Änderungen dauert Sekunden, nicht Minuten. Datei-Watcher für lokale Änderungen, periodischer Abgleich als Primärmechanismus, weil SMB Fremdänderungen nicht meldet. |
| **Anzeigen** | Virtualisiertes Kachelraster, das auch bei hunderttausend Einträgen flüssig bleibt. Persistenter WebP-Thumbnailcache in drei Größen; nach dem ersten Durchlauf wird kein Originalbild mehr angefasst. |
| **Suchen** | Volltext über FTS5 sowie Suche nach einzelnen Prompt-Segmenten. Rechtsklick auf einen Satz im Editor findet jeden anderen Prompt, der ihn enthält. |
| **Analysieren** | Prompts werden in Sätze zerlegt (nicht in Komma-Tags — das reale Format ist Langform-Prosa). Der Editor hebt hervor, was diesen Prompt von den anderen unterscheidet, und was bloß Textbaustein ist. |
| **Vergleichen** | Zwei Prompts Zeile für Zeile in WinMerge-Darstellung, mit Wortverfeinerung, synchronem Scrollen und Übersichtsleiste. Einzelne Unterschiede lassen sich übernehmen. |
| **Nachverfolgen** | Ähnliche Prompts werden zu Familien gruppiert; die Prompt Evolution zeigt die Familie als Stammbaum, dessen Knoten die **Änderung zum Elter** benennen. |
| **Aushalten** | Ein NAS-Ausfall legt nichts lahm: Circuit-Breaker, aggregiertes Fehlerprotokoll, Problemliste in der Statusleiste. Kein Dialog, der den Betrieb unterbricht. |

Oberfläche und Farbschema folgen VS Code, hell und dunkel, dem Windows-Modus folgend oder
fest eingestellt.

---

## Ausführen

Es wird bewusst **kein virtuelles Environment** verwendet (Projektvorgabe); die benötigten
Pakete liegen in der genutzten Python-Installation. Wer neu aufsetzt:

```
pip install -r requirements.txt        # PyQt6, Pillow, numpy
pip install -r requirements-dev.txt    # zusätzlich pytest
```

```
python -m app                   # Anwendung starten
python -m app --check           # nur Startsequenz prüfen, ohne Oberfläche
python -m app --json            # Systemzustand maschinenlesbar
python -m app --bootstrap PFAD  # mit isolierter Konfiguration (Zweitinstanz)
```

Beim ersten Start ist noch kein Bibliotheksordner eingebunden:
**Datei → Bibliotheksordner hinzufügen**. Danach läuft der Abgleich automatisch, bei jedem
weiteren Start inkrementell. Die Analyse (**Analyse → Prompts analysieren**, danach
**Ähnlichkeiten und Familien berechnen**) läuft im Hintergrund und lässt sich jederzeit
abbrechen.

### Bedienung

| Aktion | Wirkung |
|---|---|
| Klick auf Ordner | lädt den Inhalt in das Kachelraster |
| Rechtsklick auf Ordner | in neuem Tab öffnen, neu einlesen, im Explorer öffnen |
| Klick auf Kachel | lädt Bild und Prompt in die Vorschau |
| Doppelklick auf Kachel | öffnet einen Detailtab mit Bild und Prompt |
| Rechtsklick auf Kachel | ähnliche Prompts, Promptfamilie, Evolution, für Vergleich markieren |
| Rechtsklick im Editor | Segment suchen, ähnliche Verwendungen finden |
| `Strg+F` | Suchfeld fokussieren |
| `Strg+Enter` | aktuelles Ergebnis als eigenen Tab festhalten |
| `Strg+D` | Vergleich der beiden markierten Bilder |
| `Strg+N` / `Strg+Umschalt+N` | neueste Prompt-Paare bzw. neueste Vorlagen |
| `Strg+,` | Einstellungen |
| `F5` | Bibliothek abgleichen |
| `Strg` + Mausrad im Raster | Kachelgröße ändern |
| Mausrad in der Bildansicht | Zoom; Ziehen schwenkt |

---

## Aufbau

Der Datenfluss geht in eine Richtung, und `tests/test_architecture.py` erzwingt das:
Importe entgegen der Richtung, SQL außerhalb der Repository-Schicht und Dateizugriffe
außerhalb von `app/filesystem` lassen den Testlauf scheitern.

```
core · dto · cache          Querschnitt, keine Rückwärtsabhängigkeit
      ↑
database · config           Verbindungen, Writer-Thread, Migrationen, Einstellungen
      ↑
repositories                der einzige Ort mit SQL
      ↑
filesystem · analysis       Scanner, NAS-Breaker, Thumbnails · Parsing, Ähnlichkeit, Diff
      ↑
jobs · services             drei Threadpools · Qt-Signale nach außen
      ↑
gui                         Fenster, Tabs, Editor, Vergleich, Evolution
```

| Baustein | Ort |
|---|---|
| Fehlerobjekte, Abbruchtoken, Pfade, Textdekodierung | `app/core/` |
| Bootstrap-Datei und Einstellungsregistry | `app/config/` |
| Verbindungen, serialisierter Schreibthread, Migrationen | `app/database/` |
| Ordner, Bilder, Prompts, Segmente, Familien, Einstellungen, Fehler | `app/repositories/` |
| Scanner, NAS-Circuit-Breaker, Dateizugriff, Thumbnail-Cache, atomares Schreiben | `app/filesystem/` |
| Parsing-Engine: Formate, Sektionen, Gewichte | `app/analysis/parsing/` |
| Ähnlichkeit, Familien, Varianten, Evolution | `app/analysis/` |
| Zeilenvergleich: Ausrichtung, Wortverfeinerung, Blöcke, Übernahme | `app/analysis/textdiff/` |
| JobManager mit drei Pools, Scan-, Thumbnail-, Analysejobs | `app/jobs/` |
| Image-, Prompt-, Index-, Analysis-, Compare-, Evolution-, System-, Watch-Service | `app/services/` |
| Hauptfenster, Ordnerbaum, Kachelraster, Bildansicht, Editor, Statusleiste | `app/gui/` |

### Zwei Entscheidungen, die alles andere prägen

**Genau ein Schreibthread, beliebig viele Leser.** SQLite im WAL-Modus erlaubt das.
Mehrere schreibende Threads laufen unter Last sporadisch auf `database is locked` — ein
Fehlerbild, das schwer zu reproduzieren und teuer zu reparieren ist. Die Beschränkung ist
architektonisch, nicht per Wiederholung abgesichert. Daraus folgt die zweite Regel: **der
GUI-Thread wartet nie auf den Schreibpfad.** Er reiht ein und zeichnet sofort; die
Warteschlange ist FIFO und wird beim Beenden geleert.

**Der normalisierte Satz ist die Analyseeinheit, nicht das Komma-Tag.** Die Prompts dieses
Projekts sind Prosa mit CAPS-Sektionen und Bullet-Parametern, kein A1111-Tagformat. Sie
entstehen durch Kopieren und Abwandeln, weshalb ganze Sätze wortgleich wiederkehren — 41 %
aller Prompts der Referenzbibliothek beginnen mit demselben Textbaustein. Segmentsuche,
Vergleich, Familien und Evolution ruhen alle darauf.

Die vollständige Architektur samt Abweichungsregister steht in
[doc/ARCHITECTURE.md](doc/ARCHITECTURE.md), der Projektstand in
[doc/STATUS.md](doc/STATUS.md). Die ursprüngliche Spezifikation liegt unverändert als
`doc/00_` bis `doc/11_` daneben.

---

## Tests

```
python -m pytest
```

830 Tests, 4 übersprungen. Die GUI-Tests laufen über die Offscreen-Plattform von Qt und
brauchen keinen Bildschirm; ein paar Theme-Tests überspringen sich dort selbst, weil die
Offscreen-Plattform kein dunkles Schema kennt. Vollständig laufen sie mit:

```
QT_QPA_PLATFORM=windows python -m pytest tests/test_theme.py
```

Die Tests sind zu einem guten Teil **Regressionstests mit Vorgeschichte**: jeder im Betrieb
gefundene Fehler hat eine eigene Testklasse, deren Docstring das Symptom und die Ursache
festhält. Wer eine davon löscht, sollte den Docstring vorher lesen.

---

## Bauen

```
python AUTOBUILD.py
```

Schreibt die Version nach `build_version.py`, baut mit PyInstaller eine einzelne `.exe`
nach `output/` und legt eine Verknüpfung daneben.

**Die Falle dieses Projekts:** In der genutzten Python-Installation liegen PyQt6 6.11 und
PySide6 6.9 nebeneinander, beide mit gleichnamigen Qt6-DLLs. Wird PySide6 zuerst geladen,
scheitert der PyQt6-Import mit `DLL load failed while importing QtCore`. `AUTOBUILD.py`
schließt PySide6 deshalb ausdrücklich aus, und `tests/conftest.py` legt die Bindung fest.
Beim Hinzufügen einer Abhängigkeit prüfen, ob sie PySide6 oder `qtpy` nachzieht.

---

## Werkzeuge

```
python tools/gen_testlib.py ZIEL --pairs 2000 --folders 8 --orphans 4
```

Erzeugt Bild-/Prompt-Paare im realen Promptformat des Projekts — Prosa-Absätze,
CAPS-Sektionen, Bullet-Parameter, Negativblock — mit variierenden Sätzen.

```
python tools/bench_analysis.py --prompts 20000 --families 500
```

Erzeugt einen Korpus mit bekannter Wahrheit und misst, wie viele Paarvergleiche die
Kandidatenauswahl einspart und ob die vorgegebenen Familien wiedergefunden werden. Nützlich
zum Kalibrieren von `analysis.cluster_threshold` auf die eigene Bibliothek.

---

## Ablageorte zur Laufzeit

Alles liegt unter `%LOCALAPPDATA%\PicPromptEditor\` und ist über `bootstrap.json`
verschiebbar. Nichts davon gehört ins Repository (siehe `.gitignore`):

```
bootstrap.json      Datenbankpfad, Cachepfad, Logging-Ziel
library.db          die einzige Datenbank (WAL-Modus, Schema v2)
thumbnails/         persistenter WebP-Cache, zweistufig geshardet
logs/               rotierende Logdateien
```

Die Datenbank hält nur Metadaten, Prompttexte, Analyseergebnisse und Cacheverwaltung —
keine Bilder, keine BLOBs.

---

## Bekannte Lücken

Ehrlicher Stand statt Wunschliste:

* **Fünfzehn der 69 Einstellungen werden nirgends gelesen.** Sie stehen im Dialog und tun
  nichts; `tests/test_architecture.py` führt die Liste, damit sie nicht wächst. Drei davon
  sind baulich nicht erfüllbar — sie werden gebraucht, bevor die Datenbank offen ist, in
  der sie stehen.
* **Der Thumbnail-Cache wächst unbegrenzt.** `thumbnails.max_cache_mb` ist eine der
  fünfzehn. Die Bausteine sind vorhanden und getestet, es fehlt der Job, der sie ruft.
* **Jenseits von 20.000 Prompts** wird die Kandidatenauswahl der Ähnlichkeit auf MinHash/LSH
  umgestellt werden müssen; die Schnittstelle ist dafür vorgesehen.

Ausführlich in [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md), Abschnitt 14.

---

## Drittanbieter

Unter `app/gui/assets/themes/theme-defaults/` liegen Dateien aus Visual Studio Code
(© Microsoft, MIT-Lizenz): die Ordner- und Dokumentsymbole werden ausgeliefert, die
Farbwerte der Themes „Dark Modern" und „Light Modern" sind als Konstanten nach
`app/gui/theme.py` übernommen. Vor einer Veröffentlichung gehört der Lizenztext beigelegt.
