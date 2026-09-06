# PicPromptEditor — Konsolidierte Architektur

Dieses Dokument führt die zwölf Spezifikationsdateien `00_` bis `11_` zu einer
einheitlichen Architektur zusammen, wie es `00_Project_Overview.txt` verlangt.
Es ist ab hier die maßgebliche Referenz. Die Spezifikationsdateien bleiben
unverändert als Anforderungsquelle bestehen.

Wo diese Architektur von einer Spezifikationsdatei abweicht, ist das an Ort und
Stelle begründet und zusätzlich in [Abschnitt 12](#12-abweichungsregister)
gesammelt. Abweichungen gibt es nur dort, wo die Vorgabe in sich widersprüchlich,
technisch nicht umsetzbar oder bei der Zielgröße der Bibliothek nicht tragfähig
ist.

**Stand:** Meilenstein M10 umgesetzt, Datenbankschema v2. Damit steht alles
von der Startsequenz bis zur Paketierung: Konfiguration, Fehlerpipeline,
Scanner, Thumbnail-Cache, JobManager, Services, Hauptfenster mit Ergebnis- und
Detailtabs, die Parsing-Engine mit Segmentsuche und Editor-Hervorhebung,
Ähnlichkeit mit Familien und Varianten, die neu gebaute Vergleichsengine mit
WinMerge-Darstellung, die Prompt Evolution mit zwischengespeicherten Bäumen,
Datei-Watcher und periodischer Abgleich, Einstellungsdialog und Dunkelmodus im
VS-Code-Aussehen sowie der Bau der .exe über `AUTOBUILD.py`. Abgedeckt durch
830 Tests (4 übersprungen, siehe [Abschnitt 13](#13-meilensteine)).

---

## Inhalt

1. [Leitentscheidungen](#1-leitentscheidungen)
2. [Schichtenmodell](#2-schichtenmodell)
3. [Nebenläufigkeit](#3-nebenläufigkeit)
4. [Datenbank](#4-datenbank)
5. [Dateisystem und NAS](#5-dateisystem-und-nas)
6. [Prompt-Parsing](#6-prompt-parsing)
7. [Analyse](#7-analyse)
8. [Caches](#8-caches)
9. [Fehlerbehandlung](#9-fehlerbehandlung)
10. [Konfiguration](#10-konfiguration)
11. [Oberfläche](#11-oberfläche)
12. [Abweichungsregister](#12-abweichungsregister)
13. [Meilensteine](#13-meilensteine)
14. [Offene Punkte](#14-offene-punkte)

---

## 1. Leitentscheidungen

Fünf Entscheidungen prägen alles Weitere.

**Der normalisierte Satz ist die atomare Analyseeinheit, nicht das Komma-Tag.**
Die Prompts dieses Projekts sind Langform-Prosa mit CAPS-Sektionen, Bullet-
Parametern und Tag-Listen — kein A1111-Tagformat. Sie entstehen durch Kopieren
und Abwandeln, weshalb ganze Sätze wortgleich wiederkehren. Das ist am
Beispielprompt nachgemessen (siehe [Abschnitt 6](#6-prompt-parsing)) und trägt
Segmentsuche, Vergleich, Varianten und Evolution gleichermaßen.

**Genau ein Schreibthread auf die Datenbank, beliebig viele Leser.** SQLite im
WAL-Modus erlaubt das. Mehrere schreibende Threads laufen unter Last sporadisch
auf `database is locked` — ein Fehlerbild, das schwer zu reproduzieren und teuer
zu reparieren ist. Die Beschränkung ist architektonisch, nicht per Wiederholung
abgesichert.

**Analyse läuft pro eindeutigem Prompttext, nicht pro Datei.** Batch-
Generierungen erzeugen viele Bilder mit identischem Prompt. `prompts.text_hash`
gruppiert sie; Parsing und Ähnlichkeitsberechnung laufen einmal je Hash.

**Abgeleitete Größen werden berechnet, nicht gespeichert.** Alles, was von der
Gesamtzahl der Prompts abhängt, ändert sich bei jedem Import für sämtliche
Datensätze. Persistiert werden nur Zähler; `commonness` und `idf` liefert eine
View.

**Die Oberfläche spricht ausschließlich mit Services.** Kein SQL, kein
Dateizugriff, kein Parser, kein Cache-Objekt in der GUI-Schicht.

---

## 2. Schichtenmodell

```
                        ┌──────────────┐
                        │  gui         │  Fenster, Tabs, Widgets, Delegates
                        └──────┬───────┘
                               │  nur DTOs
                        ┌──────▼───────┐
                        │  services    │  PromptService, ImageService, …
                        └──┬────────┬──┘
              ┌────────────┘        └────────────┐
       ┌──────▼───────┐                   ┌──────▼───────┐
       │  analysis    │                   │ repositories │  einziger SQL-Ort
       └──────┬───────┘                   └──────┬───────┘
              │                                  │
              └───────────┬──────────────────────┘
                   ┌──────▼───────┐
                   │  filesystem  │  einziger NAS-/Dateizugriff
                   └──────────────┘

Querschnitt:  core (Fehler, Abbruch, Pfade, Text) · cache · config · jobs · dto
```

Rückwärtsabhängigkeiten sind verboten. Konkret: `analysis` importiert nichts aus
`gui` oder `services`; `repositories` kennt keine Widgets; `filesystem` enthält
keine Fachlogik.

**Diese Regeln werden automatisch geprüft.** `tests/test_architecture.py`
zerlegt jede Quelldatei per AST und schlägt fehl bei einem Import entgegen der
Datenflussrichtung, bei SQL außerhalb der Repository-Schicht, bei Datei- oder
Prozesszugriffen außerhalb von `app/filesystem` und bei einem Paket ohne
Schichtvertrag im `__init__.py`. Geprüft wird der Quelltext, nicht die Laufzeit:
ein verbotener Import fällt auch dann auf, wenn der Zweig nie ausgeführt wird.

Ohne diese Prüfung erodiert die Schichtung unbemerkt. Beim Bau von M1 hat sie
drei bereits eingebaute Verstöße gefunden, die in einer Sichtprüfung
durchgegangen wären:

| Verstoß | Behebung |
|---|---|
| `gui/status/problem_dialog.py` importierte `ErrorEntry` aus einem Repository | `ErrorEntry` liegt jetzt in `app/dto/` |
| `IndexService` und `SystemService` enthielten SQL | verschoben nach `ImageRepository.reset_signatures()`, `PromptRepository.reset_signatures()`, `StatsRepository.all_counters()` |
| `ImageService` öffnete Dateien und startete Prozesse | neu: `app/filesystem/file_access.py`, der Service delegiert nur noch |

| Paket | Verantwortung | Darf nicht |
|---|---|---|
| `app/core` | Fehlerobjekte, `CancellationToken`, Pfad- und Textprimitive | irgendeine andere Projektschicht importieren, **auch kein Qt** |
| `app/config` | Bootstrap-Datei, Einstellungsregistry, `SettingsService` | Einstellungen an `SettingsService` vorbei lesen |
| `app/database` | Verbindungen, Pragmas, Writer-Thread, Migrationen | fachliches SQL enthalten |
| `app/repositories` | sämtliches SQL, liefert DTOs oder Primitive | `sqlite3.Row` nach außen geben |
| `app/filesystem` | Scanner, Watcher, Reconciler, NAS-Health, Thumbnail-Erzeugung | Analyse- oder Anzeigelogik |
| `app/cache` | LRU, RAM-Thumbnails, Prompt-Cache | Dateien anfassen (das macht `filesystem`) |
| `app/analysis` | Parsing, Statistik, Ähnlichkeit, Cluster, Varianten, Evolution | Widgets kennen, direkt auf Dateien zugreifen |
| `app/jobs` | JobManager, Pools, Jobtypen | GUI-Objekte berühren |
| `app/services` | die einzige öffentliche API für die GUI | rohe SQL-Ergebnisse durchreichen |
| `app/dto` | eingefrorene Dataclasses als Schichtgrenze | Verhalten enthalten |
| `app/gui` | Darstellung und Interaktion | SQL, Dateizugriff, Parsing, Analysealgorithmen |

`app/core` ist bewusst Qt-frei. Dadurch sind Analyse, Repositories und
Dateisystemcode ohne `QApplication` testbar: von den 830 Tests brauchen nur die
Job-, Service- und Fenstertests überhaupt eine Qt-Instanz. Die Brücke zu
Qt-Signalen entsteht erst in `app/gui/status/` — dort meldet sich der
`SystemService` als Sink am Qt-freien `ErrorReporter` an und übersetzt dessen
Meldungen in Signale.

Ergänzungen gegenüber `09_Layered_Architecture.txt`: `core/` (die Fehler- und
Abbruchprimitive brauchen *alle* Schichten, ohne dass eine untere eine obere
kennt), `dto/` als eigenes Paket (sonst wandert die GUI-Grenze in die Services)
und `cache/` (in `07_` beschrieben, in der Ordnerliste von `09_` vergessen).

---

## 3. Nebenläufigkeit

In keiner Spezifikationsdatei behandelt und zugleich das größte technische
Risiko des Projekts.

### 3.1 Threadmodell

```
GUI-Thread          nur Darstellung und Eingabe
  │
  ├─ pool:interactive  (2–3)   Suche, Prompt laden, Bild laden
  ├─ pool:io           (2–4)   NAS-Scan, Thumbnail-Erzeugung
  ├─ pool:cpu          (n−2)   Parsing, Ähnlichkeit, Cluster, Evolution
  └─ thread:db-writer  (1)     jeder Schreibvorgang auf library.db
```

`06_Background_Jobs.txt` sieht einen einzigen `QThreadPool` mit Prioritäten vor.
Das kann Interaktivität nicht garantieren: `QThreadPool` kennt einen
Prioritätsparameter, aber keine Präemption. Belegt ein langer Analysejob alle
Worker, wartet die Suche, bis er fertig ist. Auf der Zielmaschine mit 32 Kernen
käme hinzu, dass ein globaler Pool 32 gleichzeitige NAS-Zugriffe fahren würde —
beim NAS als Engpass ist das langsamer als vier.

Getrennte Pools lösen beides. Innerhalb eines Pools bleiben Prioritäten sinnvoll
und werden über den JobManager verwaltet.

### 3.2 Datenbankzugriff

| | Leser | Schreiber |
|---|---|---|
| Anzahl | eine Verbindung pro Thread (thread-local) | genau eine |
| Modus | `foreign_keys=ON`, `busy_timeout=5s`, `mmap_size=256MB` | zusätzlich `synchronous=NORMAL`, `wal_autocheckpoint` |
| Transaktionen | Autocommit, jede Abfrage ein eigener Snapshot | `BEGIN IMMEDIATE` je Batch |

`journal_mode=WAL` wird **einmal pro Datei** gesetzt, bevor die erste Verbindung
entsteht — nicht pro Verbindung. Der Journalmodus ist eine persistente
Eigenschaft der Datei. Wird er gesetzt, während schon eine Verbindung offen ist,
greift er zwar in der Datei, aber die bestehende Verbindung meldet weiter den
alten Modus. `Database.journal_mode` hält das Ergebnis fest; bleibt die
Umstellung aus, wird gewarnt, weil die gesamte Nebenläufigkeitsstrategie daran
hängt.

### 3.3 Der Schreibpfad

`DbWriter` nimmt Aufgaben als `Callable[[Connection], Any]` entgegen und liefert
ein `concurrent.futures.Future`. Drei Eigenschaften sind nicht verhandelbar:

**Batching.** Bis zu 256 Aufgaben oder 20 ms landen in einer Transaktion. Jedes
COMMIT kostet im WAL-Modus einen fsync; bei der Erstindexierung ist das der
Unterschied zwischen Sekunden und Minuten.

**Isolation über Savepoints.** Jede Aufgabe läuft zusätzlich in einem eigenen
`SAVEPOINT`. Wirft sie, wird nur sie zurückgerollt. Ohne das reißt ein einziger
fehlerhafter Datensatz tausend gute mit — `08_Error_Handling.txt` fordert
ausdrücklich Rollback ohne Kollateralschaden.

**Sichtbarkeit vor Auflösung.** Ein Future wird erst aufgelöst, *nachdem* der
Batch committet ist. Wurde es innerhalb der Transaktion aufgelöst, kehrte
`future.result()` zurück, bevor eine Leseverbindung die Änderung sehen konnte.
Das war in der ersten Fassung tatsächlich so und erzeugte einen sporadischen
Fehler in etwa jedem fünften Testlauf. Jedes Read-after-Write der Anwendung wäre
betroffen gewesen. Der Regressionstest heißt
`test_result_implies_visibility_for_readers`.

**Strikt FIFO.** Schreibvorgänge werden nicht nach Priorität umsortiert. Ein
später eingereihtes INSERT vor ein früher eingereihtes DELETE zu ziehen kehrt das
Ergebnis um. Prioritäten gehören in den JobManager, nicht in ein serialisiertes
Schreibprotokoll.

### 3.4 Was im GUI-Thread nichts zu suchen hat

Zwei Fallen, die beim Bau von M1 tatsächlich zugeschlagen haben:

**Ein Future auflösen heißt nicht, dass die Daten sichtbar sind.** Der DbWriter
löste sein Future ursprünglich innerhalb der Transaktion auf. `future.result()`
kehrte damit zurück, bevor eine Leseverbindung die Änderung sehen konnte. Das
äußerte sich als sporadischer Fehler in etwa jedem fünften Testlauf und hätte
jedes Read-after-Write der Anwendung getroffen.

**Aufräumarbeit gehört ans Ende des Jobs, nicht in den Signalhandler.** Der
Scan schloss ursprünglich mit `writer.flush()` und `recount_from_tables()` im
Signalhandler ab — also im GUI-Thread, der damit blockiert, bis alle
Schreibvorgänge des Scans committet sind. Beides läuft jetzt am Ende von
`run_job()` im Worker-Thread. Der Scanjob bekommt dafür einen `finalize`-Rückruf.

**Eine feste Frist ist im GUI-Thread immer falsch.** Der Schreibpfad ist ein
einzelner Faden mit einer Warteschlange. Wer dort mit `result(timeout=15)`
wartet, bricht ab, sobald ein größerer Vorgang davor liegt — und ein Zeitablauf
storniert die Aufgabe nicht einmal, sie läuft danach trotzdem. Genau das hat den
Einstellungsdialog abstürzen lassen; im Protokoll lief die Aufgabe siebzig
Sekunden nach dem Klick durch. Schlimmer war die Folge: die Schreibschleife des
Dialogs brach beim ersten Zeitablauf ab, und alle Einstellungen dahinter wurden
nie geschrieben.

Der Gegenweg `await_result` misst Stillstand statt Gesamtdauer und ist im
Hintergrund richtig — im GUI-Thread aber genauso falsch, nur anders: er wartet,
solange der Schreiber vorankommt, und das sind bis zu fünfzehn Minuten. So
fror „Als erledigt markieren" das ganze Fenster ein.

Daraus folgen zwei Regeln:

* **Ein Signalhandler darf nichts tun, was warten kann.** Er nimmt Daten
  entgegen und aktualisiert Widgets. Alles andere ist ein Job.
* **Der GUI-Thread wartet gar nicht auf den Schreibpfad.** Er reiht ein und
  zeichnet das Ergebnis sofort. Die Warteschlange ist FIFO und
  `Application.shutdown` leert sie — die Änderung geht nicht verloren, sie ist
  nur nicht in derselben Millisekunde lesbar. Wo das stört (die Problemliste
  liest ihre Zeilen sonst vor dem Commit zurück), wird die Anzeige
  vorweggenommen statt gewartet.

### 3.5 Veraltete Ergebnisse

Ordnerklick, Suche beim Tippen und Scroll-Anfragen laufen asynchron. Jede Anfrage
bekommt eine monoton steigende `request_id`; die Oberfläche verwirft Antworten
mit älterer Id. Ohne das landet das Ergebnis eines alten Klicks in der aktuellen
Ansicht. In keiner Spezifikationsdatei erwähnt, aber ohne Gegenmaßnahme ein
sicherer Fehler.

Abbruch ist kooperativ: `CancellationToken` mit Eltern-Kind-Kaskade, damit ein
abgelöster Suchvorgang seine Teiljobs mitnimmt.

---

## 4. Datenbank

Genau eine `library.db`. Keine Originalbilder, keine BLOBs, keine EXIF-Dumps —
nur Metadaten, Prompttexte, Analyseergebnisse und Cacheverwaltung.

### 4.1 Tabellenübersicht (Schema v2)

```
folders ──┬── images ──── thumbnails (über thumbnail_key)
          └── prompts ─┬─ prompts_fts        (FTS5, external content)
                       ├─ prompt_sections ── prompt_params
                       ├─ prompt_tokens ──── tokens ── token_frequency
                       ├─ cluster_members ── prompt_clusters ── evolution_cache
                       ├─ prompt_variants
                       ├─ prompt_neighbors
                       └─ dirty_prompts

Betrieb: settings · library_stats · analysis_state · scan_history
         error_log · system_status · schema_migrations
Views:   token_stats · v_orphans
```

23 Tabellen, 2 Views, 40 Indizes, 3 Trigger — nachgezählt an einer frisch
migrierten Datenbank. Die vier Schattentabellen, die FTS5 selbst anlegt
(`prompts_fts_data`, `_idx`, `_docsize`, `_config`), sind darin nicht
mitgezählt; sie gehören zum Index, nicht zum Schema.

### 4.2 Kernentscheidungen

**`is_present` statt `exists`.** `exists` ist in SQLite ein Schlüsselwort;
`CREATE TABLE x(exists INTEGER)` ist ein Syntaxfehler, und selbst mit Quoting
scheitert jedes unquotierte `SELECT id, exists FROM …`. Das zöge sich durch jede
SQL-Zeile des Projekts. Ebenso `thumbnail_state` statt `thumbnail_exists`.

**Paar-Identität über `(folder_id, stem_norm)`.** `stem_norm` ist der
casefold-normalisierte Dateiname ohne Endung. Windows vergleicht Pfade
case-insensitiv, ein Linux-basiertes NAS nicht — ohne normalisierte Form ist die
Zuordnung von `Bild.JPG` zu `bild.txt` vom Zufall abhängig.

`images.prompt_id` ist bewusst **nicht** unique. Liegen `bild.jpg` und `bild.png`
neben einem einzigen `bild.txt`, sollen beide Bilder den Prompt zeigen. Der
Scanner protokolliert den Fall als Warnung, verwirft aber keine Datei.

**`prompts.folder_id`** ist eine Ergänzung gegenüber `03_`. Ohne sie braucht jede
ordnerbezogene Promptabfrage einen Join über `images`, und ein Prompt ohne Bild
wäre keinem Ordner zuordenbar.

**`folders.parent_id` und `depth`** sind ergänzt, weil der Ordnerbaum der
Oberfläche aus der Datenbank aufgebaut werden muss. Ohne Elternbeziehung wäre
jede Baumdarstellung ein Dateisystemzugriff — genau das schließt `02_` aus.

**FTS5 mit `content='prompts'`.** Der Volltextindex verweist auf die Tabelle und
dupliziert den Prompttext nicht; das spart bei großen Bibliotheken etwa die
Hälfte der Datenbankgröße. Drei Trigger halten ihn synchron. Der Update-Trigger
hängt an `AFTER UPDATE OF prompt_text` — Statusfelder wie `parse_version` zu
aktualisieren löst dadurch keine Neuindizierung aus, was jeden Scan spürbar
verbilligt.

Nutzereingaben müssen vor der FTS5-Abfrage maskiert werden (Phrasen in
Anführungszeichen, Sonderzeichen der MATCH-Syntax). Ein roher Suchstring ist
sonst eine Fehlerquelle und im schlimmsten Fall eine Syntaxfalle.

**`tokens.kind`.** Die Tabelle heißt weiterhin `tokens` wie in `03_`/`04_`, hält
aber Segmente unterschiedlicher Art: `sentence`, `tag`, `param`, `header`,
`word`. Beim Prosa-Format kommen Sätze, Tag-Listen und Bullet-Parameter im selben
Prompt nebeneinander vor. Eindeutigkeit läuft über `(kind, token_hash)`, weil
ganze Sätze für einen Textindex zu lang sind.

**`token_frequency` hält nur Zähler.** `doc_count` (in wie vielen Prompts),
`total_count` (Gesamtvorkommen), `neg_doc_count` (davon negativ). Die View
`token_stats` berechnet daraus:

```
commonness = doc_count / distinct_prompt_texts     → Basisprompterkennung
idf        = ln(distinct_prompt_texts / doc_count) → Hervorhebung, Gewichtung
```

`04_` definiert `usage_count` als Zahl der Verwendungen, `05_` als Zahl der
Prompts — zwei verschiedene Größen unter einem Namen. Beide werden jetzt getrennt
geführt.

`importance` wird **nicht** persistiert. Beide Formeln hängen von der Gesamtzahl
ab; ein gespeicherter Wert müsste bei jedem Import für alle Tokens neu
geschrieben werden. Genau das verbietet `04_` mit der Forderung nach
inkrementeller Aktualisierung. Als View ist der Wert immer konsistent und kostet
nichts.

Außerdem sind es fachlich zwei Größen: Basisprompts bestehen aus den *häufigen*
Segmenten, hervorheben will man die *seltenen, kennzeichnenden*. `03_`/`05_`
verwenden für beides dasselbe Feld.

**`prompt_clusters` löst ein Fremdschlüsselproblem.** `03_` lässt
`prompt_variants.base_prompt_id` auf `prompts.id` zeigen. Ein Basisprompt, der
als Schnittmenge einer Familie entsteht, existiert aber in der Regel als kein
realer Prompt. `prompt_clusters.base_signature` hält die abstrakte Basis,
`representative_prompt_id` den echten Prompt, der ihr am nächsten liegt. Der
Fremdschlüssel bleibt gültig, die abstrakte Basis bleibt darstellbar.

**`thumbnails` als eigene Tabelle.** `07_` fordert drei Größenstufen,
`03_` gibt ein einzelnes `thumbnail_exists`-Boolean. Ein Flag kann drei Zustände
nicht abbilden. `PRIMARY KEY (thumbnail_key, size_px)` plus `last_access` ist
zugleich die Grundlage der LRU-Bereinigung mit Speicherobergrenze, die `07_`
verlangt und mit einem Boolean unmöglich wäre.

**`prompt_neighbors` speichert nur Top-k.** Alle Paare wären bei 100.000 Prompts
fünf Milliarden Zeilen. Mit `rank` im Primärschlüssel ist der Speicherbedarf
durch k begrenzt (Voreinstellung 20).

**`dirty_prompts` als Tabelle statt als Spalte.** Jobs holen ihren Vorrat atomar
mit `DELETE … RETURNING` (SQLite ≥ 3.35, hier 3.49). Zwei parallele Analysejobs
können denselben Prompt dadurch nicht doppelt bearbeiten. `analysis_state` hält
je Stufe eine `algo_version`; ein Versionssprung invalidiert eine ganze Stufe,
ohne Millionen Flags zu schreiben.

**`error_log` aggregiert.** `count` und `last_seen` statt einer Zeile je
Vorkommen, abgesichert durch `UNIQUE(severity, source, message_key, resolved)`.
Ein NAS-Ausfall während eines Scans erzeugt sonst zehntausende identische Zeilen.
`resolved` ist Teil des Schlüssels: nach dem Abhaken beginnt derselbe Fehler eine
neue Zeile.

**`schema_migrations` und `PRAGMA user_version`.** `11_` fordert automatische
Migration, `03_` liefert nur ein statisches Schema. Jede Migration läuft in einer
Transaktion; vor der ersten Änderung an einer bestehenden Datenbank wird
gesichert.

Migrationen dürfen `executescript()` **nicht** verwenden: es committet eine
offene Transaktion implizit und nimmt der Migration damit den
Transaktionsschutz. `app/database/sqlscript.py` zerlegt das Skript stattdessen
mit `sqlite3.complete_statement()` in Einzelstatements — das behandelt
Trigger-Bodies korrekt, in denen ein Semikolon das Statement nicht beendet.

### 4.3 Zeitstempel

Alle Zeiten als Zahl, nie als formatierter String. Dateizeiten als
`st_mtime_ns` (Integer), interne Zeiten als `unixepoch('subsec')` (Real).
Beim Änderungsvergleich gilt eine Toleranz (`index.mtime_tolerance_s`, 2 s
Voreinstellung), weil SMB-Freigaben Zeitstempel teils runden. Ohne Toleranz
erzeugt jeder Scan Fehlalarme und damit unnötige Rehash-Wellen.

---

## 5. Dateisystem und NAS

### 5.1 Erstindexierung und inkrementeller Abgleich

Erstlauf: vollständiger Scan aller Wurzeln, Bild und Prompt über `stem_norm`
verknüpft, Prompttext lokal gespeichert. Danach arbeiten Suche, Analyse und
Anzeige ausschließlich gegen die Datenbank.

Änderungserkennung primär über Größe und Zeitstempel. **Bilder werden
standardmäßig nicht voll gehasht.** `02_` fordert Hashwerte für alle Dateien; bei
100.000 Bildern à 2 MB wären das 200 GB über das Netzwerk. Stattdessen:

| Dateiart | Voreinstellung | Bei Verdacht |
|---|---|---|
| Promptdatei (`.txt`, klein) | Volldatei-Hash | — |
| Bild | `size + mtime_ns` | Teilhash aus ersten und letzten 64 KB plus Größe |

Volles Bild-Hashing bleibt als Einstellung (`index.hash_images`) verfügbar.
`thumbnail_key` braucht den Hash nicht: er wird wie spezifiziert aus Pfad, Größe
und Änderungsdatum abgeleitet, ändert sich also automatisch mit dem Originalbild.

### 5.2 Watcher und Reconciler

`02_` und `06_` fordern einen Datei-Watcher plus periodischen Abgleich. Zu
präzisieren ist die Rangfolge: **der periodische Reconciler ist der
Primärmechanismus**, der Watcher nur ein opportunistischer Beschleuniger.

Grund: Auf SMB-Freigaben werden Änderungen, die *andere Rechner* vornehmen,
grundsätzlich nicht gemeldet. Ein Watcher über zehntausende Verzeichnisse
sprengt zudem die Handle-Grenzen. Überwacht werden daher nur die Wurzeln und der
gerade sichtbare Ordner.

**Umsetzung (M7).** Statt der ursprünglich geplanten `watcher.py` und
`reconciler.py` in `app/filesystem` ist die Sache in zwei Teile getrennt, weil
`QFileSystemWatcher` und `QTimer` Qt-Objekte sind und die Architekturtests Qt in
`app/filesystem` verbieten:

| Teil | Ort | Inhalt |
|---|---|---|
| Entscheidung | `app/filesystem/change_tracker.py` | Entprellung, Fälligkeit, Rangfolge — Qt-frei, ohne Zeitquelle, damit ohne Ereignisschleife testbar |
| Verdrahtung | `app/services/watch_service.py` | `QFileSystemWatcher`, Takt, NAS-Probe als Job im io-Pool |

Der Tracker liest die Uhr nicht selbst, sondern bekommt die Zeit übergeben. Ein
Vierteljahr Betrieb lässt sich damit in Millisekunden prüfen
(`tests/test_change_tracker.py`).

### 5.3 NAS-Ausfall

`08_` fordert Weiterarbeit bei nicht erreichbarem NAS. Das Problem ist nicht die
Fehlerbehandlung, sondern die Blockade: ein `os.stat()` auf eine tote Freigabe
hängt lange, und parallele Jobs stauen sich auf.

`NasHealthMonitor` betreibt einen einzelnen Probe-Thread mit kurzem Zeitlimit und
einen Circuit Breaker (`closed` / `open` / `half-open`). Jede Dateisystem-
operation fragt zuerst den Breaker. Ist er offen, schlägt sie sofort fehl statt
zu blockieren; Suche, Vergleich, Analyse und Evolution laufen unverändert weiter,
weil sie ohnehin nur lokale Daten brauchen.

Lange Pfade und UNC-Freigaben werden über `app/core/paths.long_path()`
behandelt.

### 5.4 Thumbnails

Erzeugung ausschließlich im Hintergrund und Lazy: nur sichtbare Kacheln plus ein
Vorladebereich (`thumbnails.preload_rows`).

Drei Optimierungen, die in `07_` nicht stehen und je Bild spürbar zählen:

- **Ein NAS-Zugriff statt drei.** Es wird einmal die größte Stufe (512 px)
  erzeugt und 256/128 daraus im RAM heruntergerechnet.
- **`Image.draft()` bei JPEG.** Dekodiert direkt in reduzierter Auflösung,
  Faktor 4–8 schneller.
- **`ImageOps.exif_transpose()`.** Ohne das stehen gedrehte Bilder im Grid falsch.

Cachedateien liegen zweistufig geshardet (`ab/cd/<key>_256.webp`). Hunderttausend
Dateien in einem Verzeichnis machen jede Verzeichnisoperation langsam.

---

## 6. Prompt-Parsing

### 6.1 Das tatsächliche Format

`04_` beschreibt Tokenisierung am Beispiel `"masterpiece, blue eyes, cinematic
lighting"` und setzt damit implizit A1111-Tagprompts voraus. Die realen Dateien
dieses Projekts sind etwas anderes: Langform-Prosa mit gemischten Blöcken.

```
<Prosa-Absätze, durch Leerzeilen getrennt>
Pose variation: <Label + Prosa>
FITMENT: <Label + Prosa>

CAMERA & LENS SETTINGS          ← CAPS-Überschrift
•Camera: Full-frame mirrorless  ← Bullet-Parameter
•Aperture: f/2.0
•ISO: 400–640

STYLE & QUALITY TAGS            ← CAPS-Überschrift
hyper-realistic, cinematic lighting, …   ← Tag-Liste

NEGATIVE PROMPT                 ← CAPS-Überschrift, ab hier negativ
cartoon, illustration, anime, …          ← Tag-Liste
```

Ein reiner Komma-Tokenizer wäre hier weitgehend nutzlos: aus dem ersten Absatz
entstünden Satzfragmente statt wiederverwendbarer Segmente.

### 6.2 Pipeline

```
Bytes ─► Dekodierung ─► Formaterkennung ─► Sektionierung ─► Segmentierung
                                                                  │
                                              ┌───────────────────┼──────────────┐
                                              ▼                   ▼              ▼
                                        prompt_sections     prompt_tokens   prompt_params
                                                            + tokens        + parse_json
```

**Dekodierung.** Strikt: BOM, dann UTF-8, und cp1252 erst, wenn UTF-8 wirklich
scheitert. Die Beispieldatei ist UTF-8 ohne BOM mit typografischen Zeichen
(U+2022 Bullet, U+2013 Halbgeviertstrich). Als cp1252 gelesen entstünden
Mojibake-Folgen, und jedes Segment wäre ein anderes als beim nächsten Lauf. Das
erkannte Encoding wird pro Prompt gespeichert. Ein führendes U+FEFF wird auch
dann entfernt, wenn es doppelt vorliegt.

**Sektionierung.** Zeilenweise, und die Prüfreihenfolge ist bedeutungstragend:

1. Bullet-Zeile mit `Schlüssel: Wert` → `param`
2. sonst kurze Zeile ohne Satzende, ≥ 90 % Großbuchstaben → `header`
3. sonst `LABEL:` gefolgt von langem Text → beschriftete Prosa
4. sonst Textzeile → sammeln

Die Reihenfolge wurde am Beispiel korrigiert: `•ISO: 400–640` enthält keinen
Kleinbuchstaben und wurde von der Header-Regel fälschlich als Überschrift
erkannt, solange sie vor der Parameterregel lief.

**Segmentierung** je Sektionsart:

| Sektionsart | Segmenteinheit | `tokens.kind` |
|---|---|---|
| `prose` | Satz | `sentence` |
| `tag_list` | Komma-Element | `tag` |
| `params` | Schlüssel/Wert | `param` (zusätzlich `prompt_params`) |
| `header` | Überschrift | `header` |

Die Satztrennung darf nicht an `f/2.0`, `1/125s`, `3:4`, `100 %` oder `30–90`
brechen — ein Punkt zwischen Ziffern trennt nicht.

**Normalisierung.** NFKC, Whitespace zusammenziehen, Rand-Interpunktion
entfernen, casefold. Ohne das Entfernen der Rand-Interpunktion wären
`photorealism` und `photorealism.` zwei Segmente; genau dieser Fall tritt im
letzten Tag der Beispieldatei auf.

**Negativkennzeichnung.** Ab der Überschrift `NEGATIVE PROMPT` (Marker
konfigurierbar über `parsing.negative_markers`) gelten Segmente als negativ, bis
eine neue Überschrift folgt. `prompt_sections.negative` und
`prompt_tokens.negative` halten das fest.

**Gewichtungen.** `(x)` = 1.1, `((x))` = 1.21, `[x]` ≈ 0.909, `(x:1.2)` explizit,
Verschachtelung multiplikativ; dazu `<lora:name:0.8>`, `BREAK`, Prompt-Editing
`[a:b:0.5]`, Alternativen `[a|b]`, escapte Klammern. Umgesetzt als kleiner
rekursiver Parser, nicht als Regex-Kette — Regex wird bei Verschachtelung
zuverlässig falsch. Das Beispielformat nutzt diese Konstrukte nicht, das
Datenmodell hält sie aber vor (`prompt_tokens.weight`).

### 6.3 Zwei Repräsentationen

| | `prompt_tokens` | `prompts.parse_json` |
|---|---|---|
| Zweck | relationaler Index für Segmentsuche und Analyse | Struktur für Editor-Hervorhebung und Diff |
| Inhalt | prompt_id, token_id, section_id, position, weight, negative, char_start/end | Sektionen, Segmente, Spannen, Rohtextfragmente |
| Größe | ~60 Zeilen je Prompt | ein kompakter Blob je eindeutigem Text |

`04_` sieht in `prompt_tokens` keine Zeichenpositionen vor. Ohne sie lässt sich
ein gefundenes Segment nicht auf seine Stelle im Originaltext zurückbilden — die
tokenbasierte Hervorhebung der Vergleichsansicht aus `01_` wäre nicht umsetzbar.

### 6.4 Messergebnis am Beispielprompt

Der Beispielprompt (4.365 Bytes) zerfällt in **23 Sektionen** und **82 Segmente**:

| Art | Anzahl |
|---|---|
| `sentence` | 32 |
| `tag` | 38 (davon 24 negativ, 10 Style, 4 Stimmung) |
| `param` | 9 |
| `header` | 3 |

Zur Prüfung der Ähnlichkeitsannahme wurde eine Variante simuliert, wie sie beim
Iterieren real entsteht: zwei geänderte Sätze und ein geänderter Kameraparameter.

```
Segmente A = 82   B = 82   gemeinsam = 79   Jaccard = 0,929

entfernt : warm golden sunlight dominates the scene
           maintain the makeup from the first image and add smokey eyes
           aperture: f/2.0 (soft background separation, sharp subject)
ergänzt  : cold blue moonlight dominates the scene
           maintain the makeup from the first image and add natural eyes
           aperture: f/1.8 (soft background separation, sharp subject)
```

Der Diff isoliert **genau** die drei Änderungen — die übrigen 79 Segmente werden
als unverändert erkannt. Das ist der Beleg dafür, dass die Segmentmenge für
Vergleich, Varianten und Evolution die richtige Grundlage ist. Der geänderte
Parameter wird zusätzlich strukturiert erfasst, ist also auch abfragbar
(„alle Bilder mit f/2.0").

---

### 6.5 Umsetzung (M3)

| Baustein | Ort |
|---|---|
| Datenstrukturen, `PARSE_VERSION` | `app/analysis/parsing/base.py` |
| Gewichte, LoRA-Verweise | `app/analysis/parsing/tokenizer.py` |
| Prosa-Parser (Primärformat) | `app/analysis/parsing/formats/prose_sectioned.py` |
| A1111-Parser | `app/analysis/parsing/formats/a1111.py` |
| Formaterkennung, Filter | `app/analysis/parsing/pipeline.py` |
| Persistenz, Häufigkeiten | `app/repositories/token_repo.py` |
| Hintergrundlauf | `app/jobs/parser_jobs.py` |
| Service-API | `app/services/analysis_service.py` |
| Hervorhebung | `app/gui/editor/highlighter.py` |

**Die Zeichenspannen sind die fehleranfälligste Stelle.** Zwei Fehler traten beim
Bau auf und wären in der Oberfläche als verschobene Hervorhebung erschienen: bei
beschrifteten Blöcken (`FITMENT: …`) begann die Spanne beim Label statt beim
Inhalt, bei Bullet-Parametern schloss sie das Aufzählungszeichen ein. Beide
entstanden dadurch, dass der Versatz mit `line.index(...)` gesucht statt aus dem
Regex-Treffer übernommen wurde — bei `FITMENT: Follow` findet die Suche nach `F`
das `F` von FITMENT. Ein Test prüft für **jedes** Segment, dass
`text[char_start:char_end]` wieder den Segmenttext ergibt.

**Deduplizierung nach Prompttext.** Geparst wird einmal je eindeutigem
`text_hash`; das Ergebnis gilt für alle Prompts mit diesem Text. Die
relationalen Zeilen entstehen trotzdem für jeden Prompt, sonst fände die
Segmentsuche nicht alle Bilder. An der Testbibliothek: 302 Prompts, aber nur
zwei Parse-Läufe.

### 6.6 Inkrementelle Häufigkeiten

Spezifikation 04 schließt Neuberechnungen aus. `TokenRepository.sync_many`
arbeitet deshalb mit Differenzen, alles in **einer** Schreibaufgabe und damit in
einem Savepoint:

```
alte Beiträge des Prompts abziehen
  -> alte Zeilen löschen
  -> neue Zeilen schreiben
  -> neue Beiträge addieren
```

Drei Fälle, die dabei stimmen müssen und je einen Test haben:

| Fall | Erwartung |
|---|---|
| Prompt geändert | nur dieser wird neu geparst, Zähler wandern korrekt |
| erzwungener Voll-Reparse | Zähler bleiben identisch, nichts verdoppelt sich |
| Promptdatei gelöscht | Zähler sinken, `commonness` bleibt ≤ 1,0 |

Der dritte Fall war zunächst falsch. Der Scanner markiert eine verschwundene
Datei nur als abwesend, ihre `prompt_tokens` blieben aber stehen und zählten in
`doc_count` weiter mit — `commonness` stieg auf 1,67. `purge_absent_prompts()`
zieht diese Beiträge ab und entfernt Segmente, die dadurch von keinem Prompt
mehr verwendet werden. Der Aufräumschritt läuft **vor** der Abbruchprüfung des
Jobs, und `pending_count()` zählt Aufräumarbeit mit: nach dem Löschen gibt es
nichts zu parsen, aber etwas zu bereinigen.

Jeder dieser Tests vergleicht die gespeicherten Zähler zusätzlich gegen eine
unabhängige Nachrechnung direkt aus `prompt_tokens`.

### 6.7 Schema v2

`doc_count` zählt Prompt-Zeilen, die View teilte aber durch
`distinct_prompt_texts`. Bei Batch-Generierungen liefen Zähler und Nenner
auseinander: `commonness` konnte über 1 steigen, `idf` negativ werden. Migration
`m002_token_stats` setzt den Nenner auf `prompt_count`. Verifiziert wurde auch
der Upgrade-Pfad von v1 samt Sicherung (`library.pre-v2.bak`).

## 7. Analyse

Alles läuft ausschließlich auf lokalen Daten, inkrementell und je eindeutigem
Prompttext.

### 7.1 Ähnlichkeit ohne quadratischen Aufwand

`05_` fordert „für jeden Prompt eine Ähnlichkeitsanalyse", sagt aber nicht wie.
Naiv sind das bei 100.000 Prompts fünf Milliarden Paare. Dreistufig:

1. **Deduplizieren** über `prompts.text_hash`. Aus 100.000 Bildern werden
   typisch 10.000–30.000 eindeutige Texte. Der größte Einzelgewinn im Projekt.
2. **Kandidaten erzeugen** über einen invertierten Index auf den *seltenen*
   Segmenten eines Prompts (hohes `idf`). Statt aller Paare bleiben einige
   hundert Kandidaten je Prompt.
3. **Exakt bewerten** nur auf den Kandidaten, mit idf-gewichteter Jaccard:

   ```
   sim(A,B) = Σ idf(s) für s ∈ A∩B  /  Σ idf(s) für s ∈ A∪B
   ```

   Gewichtet, weil ein gemeinsamer Boilerplate-Satz weniger über Verwandtschaft
   aussagt als ein gemeinsamer seltener Satz.

Gespeichert wird nur Top-k je Prompt in `prompt_neighbors`.

Bei der aktuellen Bibliotheksgröße (unter 10.000 Paare) genügt der invertierte
Index; MinHash/LSH wird erst bei sechsstelligen Beständen nötig und ist als
austauschbare Kandidatenstufe vorgesehen.

### 7.2 Cluster, Basisprompts, Varianten

- **Cluster**: Union-Find über alle Kanten oberhalb `analysis.cluster_threshold`.
  Kein globaler Clustering-Lauf, damit ein neuer Prompt nicht alles neu berechnet.
- **Basisprompt**: die Segmente einer Familie mit
  `commonness ≥ analysis.base_prompt_min_commonness`, abgelegt als
  `base_signature`. `representative_prompt_id` ist der Medoid — der reale Prompt
  mit der höchsten mittleren Ähnlichkeit zu den übrigen.
- **Varianten**: je Mitglied die hinzugefügten, entfernten und geänderten
  Segmente gegen die Basis, abgelegt in `prompt_variants` inklusive `diff_json`.

„Geändert" heißt: ein entferntes und ein hinzugefügtes Segment mit hoher
Wortüberlappung werden als Paar erkannt, statt als unabhängiges Entfernen und
Hinzufügen. Genau so wird aus `blue eyes` → `green eyes` eine gezielte Änderung
(`05_`), nur eben auf Satzebene.

### 7.3 Evolution

`05_` beschreibt Evolutionsbäume, aber ohne Zeitachse hat der Baum keine
definierte Richtung: „Varianten entstehen durch hinzugefügte Tokens" ergibt eine
Verwandtschaft, keine Eltern-Kind-Ordnung.

Die Richtung kommt aus der Datei-Änderungszeit: Kante vom zeitlich früheren zum
nächstähnlichen späteren Prompt, aufgespannt als gerichteter Baum über dem
kNN-Graphen der Familie. Damit ist es tatsächlich eine Evolution und nicht nur
eine Ähnlichkeitshierarchie.

`evolution_cache` ist über `cluster_id` geschlüsselt, nicht über
`base_prompt_id` — ein Baum deckt eine ganze Familie ab. Zusätzlich `algo_version`
und `dirty`. Ein Evolution-Tab berechnet **nie** beim Öffnen; liegt ein gültiger
Eintrag vor, wird er sofort geladen.

### 7.4 Inkrementelle Aktualisierung

```
Datei geändert
   └─► prompts aktualisiert, dirty_prompts(stage='parse')
          └─► PromptParserJob: Segmente neu, token_frequency +/- Delta
                 └─► dirty_prompts(stage='similarity')
                        └─► kNN nur für betroffene Prompts + deren Nachbarn
                               └─► betroffene Cluster neu, deren evolution_cache.dirty=1
```

Vollständige Neuberechnung nur über „Neu indizieren" oder einen `algo_version`-
Sprung.

---

### 7.5 Umsetzung (M4)

| Baustein | Ort |
|---|---|
| Ähnlichkeit, invertierter Index | `app/analysis/similarity.py` |
| Union-Find, Basissignatur, Repräsentant | `app/analysis/clustering.py` |
| Varianten, Änderungserkennung | `app/analysis/variants.py` |
| Persistenz | `app/repositories/cluster_repo.py` |
| Hintergrundlauf | `app/jobs/analysis_jobs.py` |
| Belastungsmessung | `tools/bench_analysis.py` |

**Gerechnet wird auf Repräsentanten, gespeichert wird für alle Prompts.**
Prompts mit identischem Text sind für die Ähnlichkeit ununterscheidbar; je
Textvariante wird einer verglichen. Die Ergebnisse werden anschließend
ausgedehnt, damit jedes Bild eine Familie und eine Variante hat — sonst hätte
ein Bild, dessen Prompt zufällig doppelt vorkommt, keine Zuordnung.

**Vollständiger Austausch statt Differenzbildung.** Familien sind globale
Strukturen: ein neuer Prompt kann zwei bestehende verbinden. Ein Teilupdate
müsste solche Fälle einzeln behandeln und wäre die häufigste Quelle
inkonsistenter Anzeigen. Der Austausch ist bezahlbar, weil er nur auf
Repräsentanten arbeitet.

### 7.6 Messwerte und Kalibrierung

`tools/bench_analysis.py` erzeugt einen Korpus mit **bekannter Wahrheit** — eine
vorgegebene Zahl von Familien fester Größe — und prüft, ob die Kette sie
wiederfindet.

| Korpus | Vergleiche | statt allen Paaren | gespart | Dauer |
|---|---|---|---|---|
| 5.000 Prompts, 200 Familien | 122.684 | 12.497.500 | 99,0 % | 0,56 s |
| 20.000 Prompts, 500 Familien | 780.000 | 199.990.000 | 99,6 % | 3,22 s |

Beim 20.000er-Korpus findet die Kette bei Schwelle 0,45 **genau die 500
Familien zu je 40 Mitgliedern** wieder — die Wahrheit vollständig, ohne
Verkettung.

**Die Schwellwerte sind gemessen, nicht geraten.** Die ursprünglich aus der
Spezifikation abgeleiteten Werte (Ähnlichkeit 0,55 / Familie 0,70) fanden auf
demselben Korpus **null** Familien. Der Zusammenhang:

| Familien-Schwelle | Familien | größte | zugeordnet (von 20.000) |
|---|---|---|---|
| 0,45 | 500 | 40 | 20.000 |
| 0,50 | 500 | 40 | 19.994 |
| 0,55 | 500 | 38 | 14.451 |
| 0,60 | 565 | 20 | 4.760 |
| 0,70 | 0 | 0 | 0 |

Geliefert werden jetzt 0,35 (Speichern) und 0,45 (Familie). Der Speicherwert
liegt bewusst darunter, damit sich die Familien-Schwelle senken lässt, ohne die
Ähnlichkeiten neu zu berechnen.

**Der Familien-Schwellwert ist der empfindlichste Regler des Systems.** Union-Find
verkettet transitiv: eine Kette A–B–C–D verbindet A und D, auch wenn sie sich
nicht ähneln. Zu niedrig gesetzt entsteht eine einzige nutzlose Familie mit
fast der ganzen Bibliothek. Die Belastungsmessung warnt ausdrücklich, wenn eine
Familie mehr als die Hälfte des Bestands umfasst.

### 7.7 Zwei Rechenentscheidungen mit Begründung

**Geglättetes idf für die Ähnlichkeit, ungeglättetes für die Hervorhebung.** Die
View liefert `ln(N/df)`; ein Segment in *jedem* Prompt bekommt damit exakt 0.
Für die Hervorhebung ist das richtig — Allerweltssätze sollen nicht auffallen.
Für die Ähnlichkeit ist es fatal: in einer homogenen Bibliothek trägt dann
*kein* gemeinsames Segment etwas bei, jede Ähnlichkeit ist null, und es entsteht
keine einzige Familie. `ClusterRepository.IDF_FLOOR` addiert deshalb einen
Sockel (die übliche Glättung): Textbausteine zählen wenig, aber nicht nichts.

**Dice statt Jaccard für die Änderungserkennung.** Für
`warm golden sunlight dominates the scene` gegen
`cold blue moonlight dominates the scene` liefert Jaccard 0,33 und Dice 0,50.
Bei Schwelle 0,4 verfehlt Jaccard genau die Änderung, um die es geht, während
Dice sie erkennt und unverwandte Sätze weiterhin bei 0,0 lässt.

**Der Repräsentant wird nach Abdeckung gewählt, nicht nach Jaccard.** Bei einer
Basis `{100, 101}` bewertet Jaccard einen Prompt `{100}` mit 0,5 und einen
Prompt `{100, 101, 300, 301, 302}` mit 0,4 — der Prompt mit der *halben* Basis
gäbe also den Basisprompt ab, und jedes Geschwister sähe so aus, als hätte es
den fehlenden Satz ergänzt. Ausgewählt wird deshalb nach Abdeckung der
Signatur, bei Gleichstand nach wenigsten Zusätzen.

### 7.8 Evolution (M6)

| Baustein | Ort |
|---|---|
| Baumbildung | `app/analysis/evolution.py` |
| Cache-Zugriff | `app/repositories/evolution_repo.py` |
| Hintergrundlauf | `app/jobs/evolution_jobs.py` |
| Service | `app/services/evolution_service.py` |
| Darstellung | `app/gui/evolution/evolution_tab.py` |

**Die Richtung kommt aus der Zeit.** Spezifikation 05 verlangt Evolutionsbäume,
sagt aber nicht, woher die Eltern-Kind-Ordnung stammt: „Varianten entstehen durch
hinzugefügte Tokens" beschreibt eine Verwandtschaft, keine Richtung. Aus zwei
ähnlichen Prompts allein lässt sich nicht ablesen, welcher aus welchem entstand.

```
Wurzel  = der älteste Prompt der Familie (prompts.modified_time)
Elter   = der ähnlichste Prompt, der ZEITLICH FRÜHER liegt
```

Damit ist der Baum wohldefiniert und **kreisfrei ohne Zyklenprüfung**: jeder
Knoten außer der Wurzel hat genau einen Elter, und der ist strikt älter. Bei
gleichem Zeitstempel entscheidet die Prompt-Id, damit der Baum reproduzierbar
bleibt.

**Die Ähnlichkeit ist dieselbe wie in der Familienbildung** (idf-gewichtete
Jaccard). Würde hier ungewichtet gerechnet, könnte der Baum eine andere Struktur
zeigen als die Familie, zu der er gehört.

**Knoten werden nach ihrer Änderung benannt, nicht nach ihrem Promptanfang.**
Beim ersten Bau zeigte der Baum fünfmal denselben Text — die erste Zeile ist
innerhalb einer Familie meist bei allen gleich und als Beschriftung damit
wertlos. Aussagekräftig ist, was den Knoten vom Elter unterscheidet. Gemessen an
einer echten Familie:

```
A cinematic editorial portrait of a young woman…      Ausgangsprompt
  ergänzt: she wears a sleek avant-garde crown          sim 0,78
    ergänzt: maintain the makeup and add smokey eyes    sim 0,74
      geändert zu: cold blue moonlight dominates…       sim 0,62
    ergänzt: her earrings match the crown in style      sim 0,68
```

Die Änderungserkennung ist dieselbe wie in Variantenbildung und
Vergleichsansicht (Dice ≥ 0,4): aus „Satz weg, Satz neu" wird eine Änderung an
derselben Stelle.

**Ein Tab rechnet niemals beim Öffnen.** Spezifikation 06 verlangt das
ausdrücklich, und der Service setzt es wörtlich um: `tree_for_image_async()`
liefert entweder sofort einen fertigen Baum oder meldet, dass gerechnet werden
muss. Ein stilles Nachrechnen beim Klick wäre bei großen Familien eine Wartezeit
ohne Erklärung. Gemessen: zweiter Zugriff aus dem Cache in **21 ms**.

`tree_json` ist deshalb die **fertige Struktur**, keine Sammlung von Kanten, die
die Oberfläche erst zusammensetzen müsste. Ein unbrauchbarer Eintrag gilt als
fehlend statt als Fehler.

**Große Familien werden beschnitten** (`analysis.evolution_max_nodes`, 500). Die
Elternsuche ist innerhalb einer Familie quadratisch — richtig bei Hunderten
Mitgliedern, nicht bei Zehntausenden. Behalten werden die **jüngsten**: die
aktuelle Entwicklung ist interessanter als ihre Vorgeschichte, und ein Baum mit
tausend Knoten wäre ohnehin nicht lesbar.

**Darstellung als `QTreeWidget`, nicht als gezeichnete Grafik.** Ein Baum ist
genau das, was hier dargestellt wird; die Baumdarstellung ist keine Notlösung.
Sie virtualisiert und bleibt bei fünfhundert Knoten flüssig, während eine
`QGraphicsScene` Layout und Kollisionen selbst rechnen müsste. Auf- und
Zuklappen, Tastaturnavigation und Sortierung gibt es geschenkt.

## 8. Caches

| Cache | Ort | Invalidierung |
|---|---|---|
| Thumbnails persistent | lokale WebP-Dateien, geshardet | neuer `thumbnail_key` |
| Thumbnails im RAM | LRU, `cache.ram_thumbnails_mb` | Verdrängung |
| Prompttexte | `prompts.prompt_text` in der Datenbank | Datei geändert |
| Volltext | `prompts_fts` | Trigger |
| Segmente und Zähler | `tokens`, `token_frequency` | Delta beim Import |
| Ähnlichkeit | `prompt_neighbors` | `dirty_prompts` |
| Cluster | `prompt_clusters`, `cluster_members` | `dirty_prompts` |
| Evolution | `evolution_cache` | `dirty` / `algo_version` |

Für Originalbilder gibt es bewusst keinen Cache. Sie bleiben auf dem NAS und
werden nur für Detailansicht, Vergleich oder das Öffnen im Windows-Viewer
geladen.

Der persistente Thumbnail-Cache wird über `thumbnails.last_access` per LRU
begrenzt (`thumbnails.max_cache_mb`).

---

## 9. Fehlerbehandlung

Vier Stufen: `INFO`, `WARNING`, `ERROR`, `CRITICAL`.

```
Komponente ──► AppError ──► ErrorReporter ──┬─► LoggingSink   (rotierende Datei)
                                            ├─► DatabaseSink  (error_log, ab WARNING)
                                            └─► GUI-Brücke    (Statusleiste, ab M1)
```

`AppError` ist eingefroren und damit threadsicher weitergebbar. `ErrorReporter`
ist Qt-frei; ein Sink, der selbst wirft, darf die Verteilung an die übrigen nicht
verhindern, sonst reißt ein defekter Logger die Fehlerbehandlung mit.

`@guarded(reporter, source, default=…)` kapselt Jobfunktionen: jede Exception
wird gemeldet und ein Standardwert geliefert. `08_` verlangt, dass ein Fehler in
einem Job niemals andere Jobs oder den Pool stoppt.

`install_excepthooks()` fängt unbehandelte Exceptions aus Haupt- und
Worker-Threads als letztes Netz — kein Ersatz für lokale Behandlung.

Anzeige nicht über aufdringliche Dialoge, sondern über eine Statusleiste nach
Vorbild von Visual Studio, die bei Bedarf eine Problemliste öffnet.

Startprüfung: Existenz, `PRAGMA quick_check`, Migration. Schlägt etwas fehl,
liefert `startup()` `recovery_required=True`, statt zu starten und dann zu
scheitern.

Behandelte Fälle: NAS nicht erreichbar, Bild fehlt, Prompt fehlt, beschädigtes
Bild, beschädigte Promptdatei, fehlendes Thumbnail, Datenbankfehler, FTS5-Fehler,
Parsing-, Analyse- und Cachefehler, Jobfehler, abgebrochene Indizierung,
unvollständige Evolutionsdaten, Prozessabbruch und Stromausfall während eines
Schreibvorgangs (WAL plus Transaktionen).

Fehlende Bilder führen nicht zum Verlust des Prompts und umgekehrt. `pair_state`
und die View `v_orphans` machen Verstöße gegen „kein Bild ohne Prompt" sichtbar,
statt sie zu verwerfen — `08_` verlangt beides.

---

## 10. Konfiguration

Zweistufig, weil `11_` sonst zirkulär wäre: Datenbankpfad, Cachepfad und
Logging-Ziel werden gebraucht, *bevor* die Datenbank offen ist.

```
%LOCALAPPDATA%\PicPromptEditor\bootstrap.json   db_path, thumbnail_cache_dir,
                                                log_dir, log_level, version
library.db → Tabelle settings                   die übrigen 69 Einstellungen
```

Der Zugriff läuft wie gefordert ausschließlich über `SettingsService`.

`app/config/schema.py` ist die einzige Stelle, an der ein Parameter definiert
wird: Schlüssel, Typ, Standardwert, Gruppe, Beschreibung, Validator,
`requires_restart`. Beim Start werden fehlende Einträge ergänzt und ungültige auf
den Standardwert zurückgesetzt und protokolliert. Werte liegen danach typisiert
im Speicher — `get()` ist ein Dictionary-Zugriff und aus dem GUI-Thread
unbedenklich.

Änderungsbenachrichtigung über Callbacks statt Qt-Signale, damit auch Jobs und
Analysecode ohne `QApplication` zuhören können. Ein defekter Zuhörer darf `set()`
nicht scheitern lassen.

Eine unlesbare `bootstrap.json` wird beiseitegelegt und durch Standardwerte
ersetzt — ohne sie könnte die Anwendung nicht einmal starten, um den Fehler
anzuzeigen.

---

## 11. Oberfläche

Layout wie in `01_` vorgegeben; festes, nicht schließbares Tab `[Bibliothek]`:

```
┌────────────┬──────────────────────┬─────────────┐
│ Ordnerbaum │    Prompt Editor     │ Bildansicht │
├────────────┴──────────────────────┴─────────────┤
│              Thumbnail Explorer                 │
└─────────────────────────────────────────────────┘
```

Weitere Tabs für Vergleich, Suchergebnisse, Analysen und Evolution.

**Thumbnail Explorer.** `QListView` im IconMode ist ohne Zutun nicht ausreichend
virtualisiert. Nötig sind `setUniformItemSizes(True)`,
`setLayoutMode(Batched)`, ein eigenes `QAbstractListModel` und ein
`QStyledItemDelegate`. Das Modell hält nur leichte Tupel (id, stem,
thumbnail_key, flags); selbst 100.000 davon sind rund 10 MB und in einem
Hintergrund-Query ladbar, was die gesamte Paging-Komplexität erspart.

Fertige Thumbnails dürfen `dataChanged` **nicht** einzeln feuern. Ein
50-ms-`QTimer` fasst sie zu Bereichen zusammen; sonst entstehen tausende
Repaints pro Sekunde.

**Prompt Editor.** Zunächst schreibgeschützt (`ui.editor_read_only`, per
Projektentscheidung). Kopieren, Segment markieren, Kontextmenü mit Segmentsuche,
ähnliche Verwendungen und Evolution. Hervorhebung über eine in den Highlighter
gespiegelte Segment-`idf`-Map — reiner Speicherzugriff, keine Abfrage je Block.

Der Editor selbst bleibt gesperrt. Geschrieben wird ausschließlich über die
Übernahme eines Difference Blocks in der Vergleichsansicht — und auch dort nur
in eine **neue, vom Benutzer gewählte Datei**, nie in eine Vorlage
(Abweichungen Nr. 31 und 33). Wie das abläuft, steht in
[Abschnitt 11.2](#112-vergleichsansicht-m5-neu-gebaut-in-m9).

**Vergleichsansicht.** Bilder außen, Editoren innen, synchrones Scrollen. Das
hier ursprünglich beschriebene Verfahren — Diff über die Segment-Id-Listen mit
Rückbildung über `char_start`/`char_end` — ist in M9 ersetzt worden; genau diese
Rückbildung hat die Markierungen wiederholt verschoben. Der heutige Aufbau steht
in [Abschnitt 11.2](#112-vergleichsansicht-m5-neu-gebaut-in-m9).

Die Farbvorgabe aus `01_` (Gelb/Grün/Rot/**Weiß**) gilt nur für den hellen Modus;
Weiß für „identisch" ist im dunklen Modus falsch. Stattdessen: „identisch" =
keine Hervorhebung, und die drei Diff-Farben als semantische Tokens mit heller
und dunkler Variante.

**Theme.** Qt 6.11 liefert `QStyleHints.colorScheme()` und `colorSchemeChanged`;
damit ist die automatische Übernahme des Windows-Modus sauber lösbar.

**Sprache.** Bezeichner im Code englisch, Oberflächentexte deutsch. Die in `11_`
geforderten Sprachoptionen sind in M8 **gestrichen** worden: es gibt kein
`tr()`, keine Übersetzungsdateien und keinen Regler dafür. Ein einsprachiges
Werkzeug für einen einzigen Benutzer trägt den Apparat nicht.

---

### 11.1 Tabarchitektur (M2)

Zusatztabs muessen selbsttragend sein. Spezifikation 01 legt Ordnerbaum, Editor
und Bildansicht *in* das Bibliothekstab; ein weiteres Tab ersetzt diese Ansicht
vollstaendig. Ein Ergebnistab, der nur Kacheln zeigt, wuerde bedeuten, dass man
zum Lesen eines Prompts zurueckwechseln muss.

```
Bibliothekstab              Ergebnistab                   Detailtab
+------+--------+------+    +------------+----------+     +----------+----------+
| Baum | Editor | Bild |    |  Kacheln   |  Bild    |     |   Bild   |  Prompt  |
+------+--------+------+    |            +----------+     |          |          |
|   Kachelraster       |    |            |  Prompt  |     |          |          |
+----------------------+    +------------+----------+     +----------+----------+
```

Damit dieselben Widgets in drei Anordnungen vorkommen koennen, ist die Vorschau
ein **Controller und kein Layout**: `PreviewController` erzeugt Bildansicht und
Editor, der Aufrufer platziert sie. Waere sie ein Widget mit festem Aufbau,
muesste eine der Anordnungen den Ladecode duplizieren.

**Signale brauchen Anfragenummern, sobald es mehrere Empfaenger gibt.** Alle
Vorschauen haengen am selben `PromptService`. Ohne Nummer landet die Antwort auf
die Anfrage des Detailtabs auch im Bibliothekstab. `prompt_ready` traegt deshalb
seit M2 eine Nummer, und `load_for_image` liefert sie zurueck; jeder Empfaenger
verwirft, was nicht seine ist. Dasselbe Muster wie bei Suche und Ordnerwechsel.

**Markierungen liegen im Dienst, nicht im Modell.** In M1 hielt das Kachelmodell
den Markierungszustand selbst. Mit mehreren Tabs waere dieselbe Datei im
Suchergebnis markiert und in der Bibliothek nicht. `SelectionService` haelt den
Zustand einmal; jedes Modell hoert auf `marks_changed` und zeichnet nur die
betroffenen Zeilen neu - nicht das ganze Modell, weil das bei hunderttausend
Kacheln eine sichtbare Verzoegerung fuer eine einzelne Markierung waere.

Hoechstens zwei Markierungen zugleich, weil der Vergleich zwei Bilder
gegenueberstellt. Eine dritte verdraengt die aelteste; das ist berechenbarer,
als sie stumm abzulehnen. Die Plakette traegt A oder B, damit vor dem Oeffnen
des Vergleichs erkennbar ist, welches Bild links landet.

**Der TabManager begrenzt die Zahl offener Tabs.** Jeder Ergebnistab haelt ein
eigenes Kachelmodell samt Vorschaubildern; unbegrenzt viele offene Tabs waeren
ein schleichendes Speicherleck. Ueber zwoelf Tabs weicht der aelteste nicht
angesehene. Doppeloeffnungen verhindert ein Schluessel je Tab
("search:…", "detail:4711").

**Ein Suchergebnis oeffnet nicht automatisch ein Tab.** Waehrend des Tippens
entstuende sonst je Tastendruck eines. Das Ergebnis erscheint zuerst im
Kachelraster der Bibliothek; Strg+Enter haelt es in einem eigenen Tab fest.

### 11.2 Vergleichsansicht (M5, neu gebaut in M9)

```
+---------+--------------------+--------------------+---------+
| Bild A  |  Prompt A      | | |  Prompt B      | | | Bild B  |
+---------+--------------------+--------------------+---------+
          ^ Zeilennummern      ^ Uebersichtsleiste
```

Bilder aussen, Editoren innen - so liegen die beiden Texte, um die es geht,
unmittelbar nebeneinander.

#### Zwei Vergleiche, und das ist Absicht

| Was | Wo | Frage, die er beantwortet |
|---|---|---|
| **Segmentvergleich** | `app/analysis/diff.py` | „Wie sehr aehneln sich diese beiden Prompts?" |
| **Zeilenvergleich** | `app/analysis/textdiff/` | „Welche Zeile gehoert neben welche?" |

Der erste zaehlt Saetze und liefert die Uebereinstimmungsquote; seine Kennzahl
treibt Familien und Varianten. Der zweite erzeugt **die gesamte Darstellung**.

Bis M8 war das vermischt: die Segmentspannen wurden auf eine zeilenweise
Ausrichtung umgerechnet, und zwar zweimal hintereinander - erst um die
verworfenen Wagenrueckluf-Zeichen (`offsets.translate`), dann um die
eingeschobenen Leerzeilen (`AlignedText.map_offset`). Jede Aenderung an der
Ausrichtung konnte die Einfaerbung verschieben. Seit M9 liefert `textdiff`
Positionen **relativ zum Zeilenanfang**; eine Umrechnung gibt es nicht mehr, und
damit auch die Fehlerklasse nicht mehr.

#### Die Kette

```
Text -> normalize -> sequence -> aligner -> intraline -> blocks -> Modell -> Renderer
```

* **normalize** - Text wird zu `SourceLine` (Nummer, Originaltext,
  Vergleichsform, Zeilenende). Die Vergleichsform entscheidet ueber gleich und
  ungleich und wird **nie** angezeigt: sonst zeigte „Gross-/Kleinschreibung
  ignorieren" den Prompt in Kleinbuchstaben.
* **sequence** - `DiffStrategy` hinter einem Protokoll. Zwei Verfahren:
  `SequenceMatcher` (Vorgabe) und **Patience**, das nur beidseitig eindeutige
  Zeilen als Anker zulaesst. Patience steht hier, weil 41 % aller Prompts dieser
  Bibliothek mit demselben Textbaustein beginnen - bei so viel Wiederholung kann
  ein Verfahren, das den laengsten gemeinsamen Block sucht, die falsche
  Wiederholung zuordnen.
* **aligner** - das Herz. Innerhalb eines Ersetzungsblocks wird **nicht** stur
  der Reihe nach gepaart, sondern rekursiv ueber das jeweils aehnlichste Paar.
  Exakte Treffer haben immer Vorrang. Die Zuordnung ist kreuzungsfrei, weil die
  Rekursion nur die Bereiche vor und hinter dem gesetzten Paar betrachtet.
* **intraline** - Wort- oder Zeichenunterschiede, als **lueckenlose
  Ueberdeckung** der Zeile. Der Renderer laeuft sie einmal durch und faerbt, was
  nicht `equal` ist; Luecken selbst auszurechnen waere fehleranfaellig.
* **blocks** - aufeinanderfolgende Nicht-Gleich-Zeilen werden **ein**
  Unterschied. Drei geaenderte Zeilen hintereinander sind ein Block, nicht drei.

#### Source Model und Display Model

```
SOURCE MODEL          DISPLAY MODEL
A                     A
B          <---->     <Platzhalter>
C                     C
```

Ein Platzhalter hat keine Zeilennummer, steht in keiner Datei und wird beim
Uebertragen in ein Einfuegen oder Loeschen uebersetzt - nicht als Leerzeile
mitkopiert. Wuerde er es, waere er beim naechsten Vergleich ein echter
Unterschied, und jede Uebernahme erzeugte neuen Muell.

Der `DifferenceBlock` fuehrt beide Zaehlweisen: `left_start`/`left_end` sind
Zeilennummern fuer die Anzeige (oder `None`), `left_slice` ist ein halboffener
Bereich in die Quellzeilenliste. Nur letzterer kann eine **Einfuegestelle**
ausdruecken: `(7, 7)` heisst „hier einfuegen, nichts entfernen".

#### Warum die Zeilen buendig bleiben

Der gemeldete Fehler war: oben buendig, nach etwas Scrollen immer weiter
auseinander. Drei Stufen halten sie jetzt zusammen, und alle drei sind noetig:

1. **Ausrichtung** - beide Seiten haben gleich viele Zeilen.
2. **Hoehenausgleich** (`row_heights.py`) - bei eingeschaltetem Umbruch belegt
   dieselbe Zeile links vier und rechts sechs Anzeigezeilen. Die kuerzere
   bekommt ueber `QTextBlockFormat.setBottomMargin` genau die Differenz.
3. **Gleichlauf** (`scroll_sync.py`) - danach eine schlichte Wertuebernahme.

Der frueher benutzte anteilige Gleichlauf stimmte nur bei 0 % und 100 %.

**Deshalb `QTextEdit` statt `QPlainTextEdit`:** letzterer zaehlt senkrecht in
Anzeigezeilen, ein Abstand unter einer Zeile ist fuer ihn nicht vorhanden - der
ganze Ausgleich waere unsichtbar. `QTextEdit` zaehlt in Pixeln.

**Zwei Messgroessen, die man nicht verwechseln darf:**
`blockBoundingRect().height()` zaehlt die Blockraender **nicht** mit, `top()`
dagegen schon. `base_heights()` liefert die Hoehe ohne Raender (fuer die
Rechnung, und damit idempotent), `effective_heights()` den tatsaechlich belegten
Platz aus den Oberkanten (fuer den Nachweis).

#### Faerbung: drei Ebenen, gemalt statt ausgewaehlt

```
Wortunterschied   ueber   Zeilenfarbe   ueber   Grundfarbe
   #F1E2AD                  #EFCB05              Base
```

Der naheliegende Weg - Extraauswahl mit `FullWidthSelection` - ist
**nachgemessen falsch**: eine solche Auswahl wird ueber die Zeichenformate
gezeichnet und verdeckt genau die Wortmarkierung. Sie verdeckt auch eine spaeter
hinzugefuegte zweite Extraauswahl; die Rangfolge laesst sich so gar nicht
herstellen.

Der Zeilenhintergrund wird deshalb in `DiffPane.paintEvent` **selbst gemalt**,
bevor Qt das Dokument zeichnet. Das loest den Platzhalter gleich mit: eine Zeile
ohne ein einziges Zeichen laesst sich ueber Zeichenformate nicht einfaerben,
ueber ein Rechteck sehr wohl.

Das Modell traegt dabei **keine Farbe** - nur Zustaende wie `changed`. Welche
Farbe das ist, entscheidet der Renderer ueber die `Palette`; im Dunkelmodus ist
es eine andere.

#### Schreiben (M9, in M10 auf "Speichern unter" umgestellt)

Der einzige Weg, auf dem diese Anwendung eine Promptdatei schreibt - und seit
M10 trifft er **niemals eine Vorlage**.

```
Uebernahme -> merge_block (nur Quellzeilen)
           -> Zieldatei vom Benutzer waehlen lassen (beide Originalpfade werden
              abgelehnt)
           -> PromptWriter.save   (atomar, Konflikt geprueft)
           -> PromptRepository.update_text  (parse_version = 0)
           -> AnalysisService.start_parsing
```

Die Reihenfolge ist unumstoesslich. Wird die Datenbank vor der Datei
geschrieben und scheitert das Schreiben, stuende dort ein Text, den es auf der
Platte nicht gibt.

Die Regel steht baulich, nicht als Vereinbarung: `PromptService` kennt keine
Methode mehr, die in `prompt.full_path` schreibt (`save_async` ist entfernt),
und `CompareTab.save_side` lehnt beide Originalpfade zusaetzlich ausdruecklich
ab - auch den der Gegenseite, denn auch die ist eine Vorlage.

* **Atomar**: Nachbardatei im **selben** Verzeichnis, flush und fsync, dann
  `os.replace`. Ueber eine Geraetegrenze hinweg waere ein Umbenennen ein
  Kopieren mit Loeschen - und damit wieder unterbrechbar.
* **Konflikterkennung** gegen Groesse und mtime, 2-Sekunden-Toleranz wie beim
  Scanner (SMB und FAT runden Zeitstempel).
* **Encoding und Zeilenenden bleiben erhalten.** Beim Setzen der Zeilenenden
  wird **erst vereinheitlicht, dann gesetzt** - der Text kommt oft schon mit
  CRLF aus der Datenbank, und ein schlichtes Ersetzen machte daraus CR CRLF.
* `parse_version = 0` macht die alte Auswertung ungueltig. Segmente und
  Aehnlichkeiten stammen aus dem alten Text; eine falsche Auskunft ist
  schlechter als keine.

**Ein Vergleichstab wird ueber `compare:A:B` geschluesselt, die Reihenfolge geht
ein.** A gegen B ist eine andere Ansicht als B gegen A, weil „nur links" und
„nur rechts" die Seiten tauschen.

## 12. Abweichungsregister

| # | Spezifikation | Abweichung | Grund |
|---|---|---|---|
| 1 | `03_`: Spalte `exists` | heißt `is_present` | `exists` ist SQLite-Schlüsselwort, unquotiert nicht verwendbar |
| 2 | `03_`: `thumbnail_exists` Boolean | Tabelle `thumbnails(key, size_px, …)` | `07_` fordert drei Größenstufen; ein Flag kann das nicht abbilden |
| 3 | `03_`/`04_`/`05_`: `usage_count` | `doc_count` + `total_count` | in `04_` und `05_` unterschiedlich definiert |
| 4 | `03_`/`04_`/`05_`: `importance` | View `token_stats` mit `commonness` und `idf` | zwei fachlich gegenläufige Größen; persistiert nicht inkrementell führbar |
| 5 | `03_`: `prompt_variants.base_prompt_id → prompts.id` | `prompt_clusters` mit `base_signature` + `representative_prompt_id` | ein abgeleiteter Basisprompt existiert als realer Prompt nicht |
| 6 | `03_`: keine Migration | `PRAGMA user_version` + `schema_migrations` | `11_` fordert automatische Migration |
| 7 | `11_`: alle Einstellungen in `settings` | Bootstrap-JSON für drei Pfade | Datenbankpfad kann nicht in der Datenbank liegen |
| 8 | `06_`: ein `QThreadPool` | drei Pools + Writer-Thread | `QThreadPool` kennt keine Präemption; NAS verträgt keine 32 Parallelzugriffe |
| 9 | `02_`: Hash aller Dateien | Bilder über Größe/Zeit, Teilhash bei Verdacht | Volldurchlauf = Vielfaches der Bibliotheksgröße über Netzwerk |
| 10 | `02_`/`06_`: Watcher | Reconciler primär, Watcher sekundär | SMB meldet Fremdänderungen nicht |
| 11 | `04_`: Komma-Tokenisierung | Sektionierung + satzbasierte Segmente | reales Format ist Langform-Prosa |
| 12 | `04_`: `prompt_tokens` ohne Spannen | `char_start`/`char_end` + `parse_json` | ohne Spannen keine Hervorhebung im Originaltext (`01_`) |
| 13 | `05_`: Ähnlichkeit ohne Verfahren | Dedup + invertierter Index + Top-k | naiv quadratisch |
| 14 | `05_`: Evolutionsbaum ohne Richtung | Zeitachse aus Datei-mtime | ohne Zeit keine Eltern-Kind-Ordnung |
| 15 | `08_`: `error_log` je Vorkommen | Aggregation über `count`/`last_seen` | NAS-Ausfall erzeugt sonst zehntausende Zeilen |
| 16 | `01_`: Weiß für „identisch" | keine Hervorhebung, Farbtokens je Modus | Weiß ist im dunklen Modus falsch |
| 17 | `03_`: Zuordnung immer 1:1 | `prompt_id` nicht unique, Konflikt wird protokolliert | `bild.jpg` + `bild.png` zu einem `bild.txt` soll keine Datei verlieren |
| 18 | `09_`: Paketliste | zusätzlich `core/`, `cache/`, `dto/` | Querschnittsprimitive ohne Rückwärtsabhängigkeit |
| 19 | `00_`: streng schichtweise Lieferung | vertikale Slices ab M1 | Hauptrisiken sonst erst am Projektende validiert |
| 20 | `01_`: Linksklick oeffnet das Bild im Viewer | Doppelklick | der einfache Klick wird zum Schwenken der Bildansicht gebraucht |
| 21 | `06_`: ein QRunnable je Aufgabe | Thumbnail-Jobs verarbeiten Stapel | zweihundert Einzeljobs sind mehr Verwaltung als Arbeit |
| 22 | `01_`: Suchergebnis oeffnet ein Tab | Ergebnis erscheint zuerst im Raster, Strg+Enter haelt es als Tab fest | bei entprellter Suche entstuende sonst je Tastendruck ein Tab |
| 23 | `01_`: unbegrenzte Zusatztabs | hoechstens zwoelf, aeltester weicht | jeder Tab haelt Vorschaubilder im Speicher |
| 24 | `03_`: `importance` als Nenner offen | Nenner ist `prompt_count` (Schema v2) | `doc_count` zaehlt Prompt-Zeilen; ein anderer Nenner liefert `commonness` > 1 |
| 25 | `04_`: Tokens sind Promptbestandteile | `tokens` haelt Saetze, Tags, Parameter und Ueberschriften, unterschieden ueber `kind` | im Prosaformat ist der Satz die wiederverwendete Einheit |
| 26 | `05_`: Aehnlichkeit fuer jeden Prompt | nur fuer Repraesentanten je Textvariante, Ergebnis ausgedehnt | textgleiche Prompts sind fuer die Aehnlichkeit ununterscheidbar |
| 27 | `05_`: inkrementelle Aktualisierung aller Analysen | Familien werden vollstaendig ausgetauscht | ein neuer Prompt kann zwei Familien verbinden; ein Teilupdate waere die haeufigste Quelle inkonsistenter Anzeigen |
| 28 | `01_`: Evolution als Stammbaum oder Git-Branch | QTreeWidget statt gezeichneter Grafik | virtualisiert und bleibt bei fuenfhundert Knoten fluessig; Auf- und Zuklappen inklusive |
| 29 | `01_`: Knoten zeigt repraesentativen Prompt | Knoten zeigt die AENDERUNG zum Elter | die erste Promptzeile ist in einer Familie meist ueberall gleich und als Beschriftung wertlos |
| 30 | `01_`: Diff-Farben Gelb/Gruen/Rot | WinMerge-Schema, beide Seiten in derselben Blockfarbe | ausdruecklicher Wunsch; hinzugefuegt/entfernt zeigt der graue Platzhalter der Gegenseite, nicht die Farbe |
| 31 | `01_`: Prompt-Editor durchgaengig schreibgeschuetzt | Rueckschreiben ueber die Uebernahme eines Difference Blocks | ein Vergleich, aus dem nichts folgen kann, halbiert seinen Nutzen; der Editor selbst bleibt gesperrt |
| 32 | `05_`: „tokenbasiert statt zeilenbasiert" | beides nebeneinander - Segmente fuer die Kennzahl, Zeilen fuer die Darstellung | die Darstellung braucht eine Zeilenzuordnung; sie aus Segmentspannen abzuleiten hat die Markierungen wiederholt verschoben |
| 33 | `01_`: Rueckschreiben in die Promptdatei | ausschliesslich Speichern in eine NEUE, gewaehlte Datei | ausdruecklicher Wunsch; baulich durchgesetzt, nicht nur vereinbart - der PromptService kennt keine Methode mehr, die in prompt.full_path schreibt |
| 34 | `01_`: eigenes Farbschema | Grundfarben und Akzent aus VS Code "Dark/Light Modern" | ausdruecklicher Wunsch; als Konstanten eingetragen statt geladen, weil kein Themewechsel vorgesehen ist |
| 35 | VS Code: Flaechen um wenige Prozent Helligkeit gestaffelt | EINE Flaechenfarbe fuer alles, Struktur ueber sichtbare Rahmen | ausdruecklicher Wunsch; die Staffelung ergab vier zufaellig wirkende Grautoene und Rahmen (#2B2B2B auf #1F1F1F), die rechnerisch da und optisch nicht vorhanden waren |
| 36 | VS Code: Akzentrahmen nur beim Fokus | jedes Bedienelement traegt ihn dauerhaft, gedaempft | ausdruecklicher Wunsch; auf gleichfarbigem Grund sind die Rahmen das Einzige, was noch gliedert. Der volle Akzent bleibt dem Fokus vorbehalten |
| 37 | `08_`: feste Frist je Schreibvorgang | `await_result` begrenzt den STILLSTAND statt der Gesamtdauer | eine feste Frist hat den Abgleich dreimal abgebrochen, obwohl der Schreiber nur mit einem grossen Vorgang davor beschaeftigt war - und eine abgelaufene Frist storniert nichts |
| 38 | `11_`: "Konfigurationsaenderungen werden sofort validiert und gespeichert" | validiert sofort, gespeichert ohne Warten | im GUI-Thread ist jedes Warten auf den Schreibpfad falsch (Abschnitt 3.4). Der Wert liegt typisiert im Speicher, die Warteschlange ist FIFO, `shutdown` leert sie - "sofort" gilt fuer die Wirkung, nicht fuer den Plattenzugriff |
| 39 | `01_`: Zusatztabs schliessen ohne Rueckfrage | ein Tab, der `has_unsaved_changes()` meldet, fragt nach | seit der Uebernahme von Difference Blocks kann in einem Tab ungesicherte Arbeit stehen. Die automatische Verdraengung bei zwoelf Tabs (Nr. 23) fragt NICHT - sie ueberspringt solche Tabs, denn ein Dialog ohne Zutun des Benutzers waere schlimmer als ein dreizehnter Tab |

---

## 13. Meilensteine

| | Inhalt | Validiert |
|---|---|---|
| **M0** ✅ | Projektstruktur, Bootstrap, Logging, Fehlerpipeline, Migrationen, Schema v1 | Fundament |
| **M1** ✅ | Scanner, Thumbnail-Cache, JobManager, Services, MainWindow mit Baum, Grid, Bildansicht, Editor, Suche | Threading, Scroll-Performance, NAS-I/O |
| **M2** ✅ | Ergebnis- und Detailtabs, Tabverwaltung, entprellte Suche mit Ordnerbereich, tabuebergreifende Vergleichsmarkierungen | Tabverwaltung, Signaltrennung |
| **M3** ✅ | Parsing-Engine, `prompt_tokens`, `prompt_params`, Segmentsuche, Editor-Hervorhebung, Schema v2 | Segmentierungsqualität, inkrementelle Häufigkeiten |
| **M4** ✅ | Ähnlichkeit, Familien, Varianten, kalibrierte Schwellwerte, Belastungsmessung | Skalierung, Trefferqualität |
| **M5** ✅ | Vergleichsansicht: Segment-Diff, Wortverfeinerung, synchrones Scrollen | Spannen-Rückbildung |
| **M6** ✅ | Evolution: Bäume aus der Zeitachse, `evolution_cache`, Baum-Tab | Baumsemantik |
| **M7** ✅ | Watcher, periodischer Reconciler, NAS-Circuit-Breaker aktiv, Abgleichprotokoll | Ausfallverhalten |
| **M8** ✅ | Einstellungsdialog, Dunkelmodus, Paketierung über `AUTOBUILD.py` (i18n gestrichen) | — |
| **M9** ✅ | Vergleichsengine neu gebaut: Zeilenzuordnung, Difference Blocks, WinMerge-Darstellung | Spannen-Rückbildung entfällt |
| **M10** ✅ | VS-Code-Aussehen, Akzentrahmen, SVG-Symbole, „Speichern unter" statt Rückschreiben | Farbtreue in beiden Modi |

i18n ist gestrichen: die Oberfläche ist durchgehend deutsch, `ui.language`
wurde aus der Registry entfernt. Die dazugehörige Zeile in `settings` räumt der
`SettingsService` beim Start weg.

Ab M1 ist die Anwendung durchgehend bedienbar. `tools/gen_testlib.py` erzeugt
synthetische Bild-/Prompt-Paare zur Belastungsmessung.

### Messwerte M1

Gemessen an einer synthetischen Bibliothek mit 2.000 Paaren (4.000 Dateien,
512-px-Bilder) auf lokaler Platte. Ein NAS ist langsamer; die Verhältnisse
bleiben.

| Vorgang | Messwert |
|---|---|
| Erstscan 4.000 Dateien (kalt) | 11,98 s |
| Erstscan 4.000 Dateien (warmer Dateisystem-Cache) | 0,85 s |
| Rescan ohne Änderungen | 0,32 s |
| Ordner mit 250 Bildern in das Grid laden | 31 ms |
| Thumbnails aus dem Original (3 Größen je Bild) | 45 Bilder/s |
| Thumbnails aus dem Plattencache | 384 Bilder/s (8,6×) |

Der Faktor 8,6 zwischen erster und zweiter Anzeige ist der eigentliche Zweck des
persistenten Caches: nach dem ersten Durchlauf wird kein Originalbild mehr
angefasst.

### Umgebungshinweis: Qt-Bindungen

In der genutzten Python-Installation liegen **PyQt6 6.11 und PySide6 6.9
nebeneinander**. Beide bringen eigene Qt6-DLLs gleichen Namens mit. Wird PySide6
zuerst geladen, scheitert ein anschließender PyQt6-Import mit
`DLL load failed while importing QtCore`.

Das ist kein Fehler dieses Projekts, trifft es aber: `tests/conftest.py` legt
deshalb `PYTEST_QT_API` und `QT_API` auf `pyqt6` fest, weil pytest-qt seine
Bindung sonst selbst wählt. Beim Hinzufügen einer Abhängigkeit ist zu prüfen, ob
sie PySide6 oder qtpy nachzieht — spätestens beim Paketieren mit PyInstaller
würde das die Anwendung zerlegen.

---

## 14. Offene Punkte

1. **Fünfzehn Einstellungen ohne Wirkung.** Nachgezählt am 06.09.2026: von den
   69 Einträgen der Registry werden fünfzehn nirgends gelesen. Sie stehen im
   Einstellungsdialog, lassen sich verstellen und tun nichts — das ist
   schlimmer als ein fehlender Regler, weil der Benutzer sich auf die Wirkung
   verlässt. Die vollständige Liste steht in
   [STATUS.md](STATUS.md); drei davon sind baulich nicht erfüllbar
   (`system.log_level`, `system.backup_on_migration`,
   `index.auto_reindex_on_schema_change` werden gebraucht, *bevor* die
   Datenbank offen ist, in der sie stehen) und gehören entweder in die
   `bootstrap.json` oder gestrichen.
2. **Der Thumbnail-Cache wächst unbegrenzt.** `thumbnails.max_cache_mb` ist
   einer der fünfzehn. Die Bausteine dafür sind vollständig vorhanden und
   getestet — `ImageRepository.stale_thumbnails`, `forget_thumbnails`,
   `thumbnail_cache_bytes`, `ThumbnailCache.delete_relative` und
   `prune_empty_dirs` —, es fehlt allein der Job, der sie ruft.
3. **Weitere Promptformate.** Die Formaterkennung ist auf einen Beispielprompt
   geeicht. Weitere echte Dateien — insbesondere ältere oder aus anderen
   Werkzeugen — würden die Regeln absichern.
4. **Bibliothekswachstum.** Unterhalb von 20.000 Paaren genügt der invertierte
   Index. Bei 28.000 Bildern und 7.000 Prompts ist diese Grenze erreicht;
   jenseits davon wird die Kandidatenstufe auf MinHash/LSH umgestellt, die
   Schnittstelle ist dafür vorgesehen.
5. **Mehrere Bilder je Prompt.** Aktuell zulässig und protokolliert. Falls das
   fachlich unerwünscht ist, wird daraus ein harter Konflikt mit Meldung.
6. **Herkunft der mitgelieferten Symbole.** Unter
   `app/gui/assets/themes/theme-defaults/` liegen Dateien aus VS Code (MIT,
   Microsoft). Die Farbwerte sind daraus abgeschrieben, die SVG-Symbole werden
   ausgeliefert. Vor einer Veröffentlichung gehört die Lizenz beigelegt.
