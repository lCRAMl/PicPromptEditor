# Projektstand und Wiedereinstieg

Diese Datei ist der Anker für die Fortsetzung. Um in einer neuen Sitzung genau
hier weiterzumachen, genügt:

> **Lies `doc/STATUS.md` und mach mit dem nächsten offenen Meilenstein weiter.**

Alles Weitere steht hier und in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Stand: M10 abgeschlossen

| Meilenstein | Inhalt | Status |
|---|---|---|
| M0 | Fundament: Bootstrap, Logging, Fehlerpipeline, Migrationen, Schema | ✅ |
| M1 | Scanner, Thumbnail-Cache, JobManager, Services, Hauptfenster | ✅ |
| M2 | Ergebnis- und Detailtabs, Tabverwaltung, Suche, Markierungen | ✅ |
| M3 | Parsing-Engine, Segmentsuche, Editor-Hervorhebung, Schema v2 | ✅ |
| M4 | Ähnlichkeit, Familien, Varianten, kalibrierte Schwellwerte | ✅ |
| M5 | Vergleichsansicht mit tokenbasiertem Diff | ✅ |
| M6 | Prompt Evolution: Bäume aus der Zeitachse, Cache, Baum-Tab | ✅ |
| M7 | Watcher, periodischer Abgleich, NAS-Breaker aktiv, Abgleichprotokoll | ✅ |
| M8 | Einstellungsdialog, Dunkelmodus, Paketierung (i18n gestrichen) | ✅ |
| M9 | Vergleichsengine neu gebaut, WinMerge-Darstellung | ✅ |
| M10 | VS-Code-Aussehen, Akzentrahmen, SVG-Symbole, Speichern unter | ✅ |

**830 Tests grün, 4 übersprungen** (ein bekannter Ausreißer, siehe unten). Datenbankschema v2. Rund 30.600 Zeilen
(21.000 in `app`, 9.600 in `tests`).

```
python AUTOBUILD.py    # .exe bauen (Version in Titel und Dateiname)
python -m app          # Anwendung starten
python -m app --check  # nur Startsequenz prüfen
python -m pytest       # 830 Tests
```

Damit ist das Programm **betriebsfest**: die Bibliothek bleibt ohne Zutun
aktuell, und ein NAS-Ausfall legt nichts lahm. M8 macht daraus ein
auslieferbares.

---

## Was M7 gebracht hat

### Neue Bausteine

| Baustein | Ort | Aufgabe |
|---|---|---|
| `ChangeTracker` | `app/filesystem/change_tracker.py` | Entscheidet **wann** abgeglichen wird. Qt-frei, ohne eigene Zeitquelle |
| `WatchService` | `app/services/watch_service.py` | Qt-Verdrahtung: `QFileSystemWatcher`, Takt, NAS-Probe |
| `ScanRepository` | `app/repositories/scan_repo.py` | `scan_history` schreiben, lesen, auf 500 Zeilen kürzen |
| `ScanHistoryDialog` | `app/gui/dialogs/scan_history_dialog.py` | Abgleichprotokoll und Überwachungszustand (Menü *Bibliothek*) |

**Abweichung vom Plan:** vorgesehen waren `app/filesystem/watcher.py` und
`app/filesystem/reconciler.py`. `QFileSystemWatcher` und `QTimer` sind aber
Qt-Objekte, und die Architekturtests verbieten Qt in `app/filesystem`. Deshalb
die Trennung in Entscheidungslogik (Qt-frei, ohne Ereignisschleife testbar) und
Verdrahtung. Siehe [ARCHITECTURE.md](ARCHITECTURE.md), Abschnitt 5.2.

### Vier Fehler, die dabei ans Licht kamen

Alle vier waren still — nichts stürzte ab, es funktionierte nur nicht.

**1. Der Circuit Breaker konnte nie öffnen.** `Scanner.scan_root()` rief am
Ende jedes Laufs bedingungslos `record_success(root)`. Damit fiel der
Fehlerzähler nach jedem Scan auf null zurück, der Schwellwert von drei wurde nie
erreicht, und der Breaker blieb dauerhaft `closed` — der ganze Mechanismus war
wirkungslos. Jetzt wird Erfolg nur gemeldet, wenn die Wurzel tatsächlich lesbar
war.

**2. Ein Ausfall mitten im Lauf lief in jede Zeitüberschreitung.** Brach die
Freigabe nach dem Einsammeln der Verzeichnisse weg, arbeitete die Schleife
stur alle restlichen Ordner ab — auf einer echten Freigabe zwanzig Sekunden
je Ordner. Der Lauf endet jetzt, sobald der Breaker öffnet. Gemessen an zwölf
Ordnern: 3 Fehlversuche statt 13.

**3. Der Breaker kannte die Wurzeln gar nicht.** `register_root()` lief nur beim
Start — zu einem Zeitpunkt, an dem `library.roots` oft noch leer ist. Für einen
unbekannten Pfad verwirft der Monitor jede Rückmeldung, `snapshot()` blieb leer,
und die NAS-Anzeige zeigte nichts. Der `WatchService` meldet die Wurzeln jetzt
selbst an.

**4. Das Abgleichprotokoll war nach Zeitstempeln nicht eindeutig sortierbar.**
`unixepoch('subsec')` liefert mehreren Läufen in derselben Millisekunde
denselben Wert; bei Gleichstand ist die Reihenfolge in SQLite beliebig. Das
Kürzen auf 500 Zeilen konnte dadurch ausgerechnet die jüngsten Einträge
löschen. Sortiert wird jetzt nach `scan_time DESC, id DESC`.

### Was sonst noch dazukam

- `IndexService.scan_started(art)` — im Menü *Bibliothek* ist während eines
  Laufs nur noch *Abgleich abbrechen* wählbar, sonst nur *Abgleichen* und
  *Neu indizieren*.
- Der DbWriter schreibt asynchron; das Protokoll wurde deshalb ohne die Zeile
  des gerade beendeten Laufs gelesen. `scan_history()` wartet jetzt kurz
  (höchstens zwei Sekunden) auf genau diesen einen Eintrag — kein voller
  `flush()`, der würde die Warteschlange eines laufenden Scans abwarten und die
  Oberfläche einfrieren.
- Die erste NAS-Prüfung nach dem Start meldet keine Entwarnung mehr. Entwarnt
  wird nur, wenn vorher gewarnt wurde.

### Neue Tests (41)

| Datei | Was geprüft wird |
|---|---|
| `tests/test_change_tracker.py` | Entprellung, Rangfolge Takt vor Watcher, Sperre während eines Scans — ohne Qt und ohne Wartezeiten |
| `tests/test_watch.py` | Protokoll, Takt löst von selbst aus, Einstellungen wirken sofort, **NAS-Prüfung blockiert den GUI-Thread nicht** |
| `tests/test_nas_outage.py` | Der Ausfall mitten im Lauf: Breaker öffnet und bleibt offen, der Scan hört auf zu fragen, lokale Daten bleiben erhalten, Rückkehr nach der Sperrzeit |

Die Ausfalltests stellen den Ausfall über `os.scandir` nach und zählen die
Zugriffsversuche. Das ist der eigentliche Prüfpunkt: nicht die Fehlermeldung,
sondern das **Ausbleiben weiterer Wartezeiten**.

---

## Bekannte Schwachstelle in der Testsuite (nicht M7, aber jetzt sichtbar)

Etwa **jeder fünfte** vollständige Lauf meldet einen SETUP ERROR beim ersten
Test nach `tests/test_tabs.py`:

```
Exceptions caught in Qt event loop:
TypeError: <lambda>() missing 1 required positional argument: 'ids'
```

Der Test selbst ist unschuldig — er fällt einzeln und in jeder Teilmenge durch.
pytest-qt sammelt Ausnahmen aus der Qt-Ereignisschleife und meldet sie erst beim
nächsten Test; ausgelöst wird sie am Ende von `test_tabs.py` durch ein
eingereihtes Signal, das nach dem `shutdown()` seiner Anwendung zugestellt wird.
Dass die Argumentzahl nicht passt, obwohl alle Verbindungen statisch
zusammenpassen, deutet auf genau diesen Fall hin: eine Verbindung auf ein
bereits abgeräumtes QObject.

Kandidaten sind die einzigen Lambdas mit einem Pflichtargument `ids` —
`tests/test_analysis.py` (Zeilen 253, 264, 275, 591) und `tests/test_gui.py`
(283, 292). **Anwendungscode ist nicht beteiligt**, nur Testverdrahtung.
Sichtbar wurde es erst durch `tests/test_watch.py`, weil das alphabetisch nach
`test_tabs.py` läuft und pytest-qt so einen Test zum Anhängen der Meldung hat.

Vorgehen für M8: in den Fixtures vor dem `shutdown()` die Ereignisschleife
leeren und die Testverbindungen wieder trennen. Ohne Behebung ist der Lauf
gelegentlich rot, ohne dass etwas kaputt ist — das ist auf Dauer teurer als die
Behebung.

---

## M8 - abgeschlossen

i18n (Mehrsprachigkeit) wurde nach Rücksprache **gestrichen**: mehrere tausend
Zeichenketten anzufassen bringt bei einem persönlichen deutschen Werkzeug
nichts. Die tote Einstellung `ui.language` ist entfernt — sie stand im Schema,
wurde aber nirgends ausgewertet.

### ✅ Dunkelmodus (Teil 1)

Der Kern war bereits gebaut: `ThemeManager` in `app/gui/theme.py` folgt dem
System und reagiert zur Laufzeit. Es fehlte genau eine Ebene.

**Der Befund:** die selbstgezeichneten Teile (Kacheln, Hervorhebungen,
Statusleiste) waren dunkel, Menüs, Knöpfe und Dialoge blieben hell — ein
Flickenteppich. Ursache ist der Qt-Stil: auf **Windows 10** beherrscht von den
vier verfügbaren Stilen nur **Fusion** ein dunkles Schema.

| Stil | Fensterfarbe |
|---|---|
| windowsvista (war aktiv) | rgb(212,208,200) — hell |
| windows11 | rgb(212,208,200) — hell |
| Windows | rgb(212,208,200) — hell |
| **Fusion** | **rgb(50,50,50) — dunkel** |

`apply_application_style()` setzt jetzt Stil und Palette, bevor das Hauptfenster
entsteht (sonst blitzt es hell auf und einzelne Widgets behalten ihre beim Bau
gelesenen Farben).

**Die Falle dabei, für den Fall dass jemand daran arbeitet:** `setColorScheme()`
wirkt über ein Ereignis, nicht sofort. `standardPalette()` liefert unmittelbar
danach unzuverlässig noch die Farben des vorherigen Schemas — besonders beim
Wechsel von „dem System folgen" auf eine feste Wahl. Weder Umsortieren noch
Wiederholen mit `processEvents()` half verlässlich. Gelöst, indem **beide
Standardpaletten einmal beim Start eingesammelt** werden; danach ist der Wechsel
sofort und in beide Richtungen sicher.

**Preis:** Fusion sieht weniger nativ aus, etwas flacher. Auf Windows 10 gibt es
für echtes Dunkel keine Alternative.

### ✅ Einstellungsdialog (Teil 2)

`app/gui/dialogs/settings_dialog.py`, erreichbar über **Datei → Einstellungen**
(Strg+,). Alle **53 Einstellungen in 9 Reitern**, vollständig aus dem Schema
erzeugt: der Dialog führt keine eigene Liste. Eine neue Einstellung in
`app/config/schema.py` erscheint von selbst.

Damit das möglich wurde, tragen die Validatoren ihre Regel jetzt als Attribut
mit sich (`bounds` bei Bereichen, `allowed` bei Auswahlen), abrufbar über
`SettingDef.bounds` und `SettingDef.allowed`. 30 Einstellungen haben einen
Bereich, 5 eine Auswahl. Ohne das müsste der Dialog dieselben Grenzen ein
zweites Mal führen — und das Schema ist ausdrücklich die *eine* Stelle dafür.

Eingabefelder nach Typ: bool → Haken, int/float → Zahlenfeld mit Bereich aus dem
Validator, str mit Auswahl → Klappliste, str → Textfeld, list → Listeneditor
(mit Ordnerdialog bei `library.roots`).

Drei Dinge, die sonst still schiefgingen:

1. **Analyseparameter stoßen eine Neuanalyse an.** Genau der Fund aus der
   Kalibrierung: ohne Anstoß bleibt eine Schwellwertänderung wirkungslos. Der
   Dialog fragt nach, statt ungefragt zu rechnen — bei einer großen Bibliothek
   dauert der Lauf.
2. **`requires_restart` ist sichtbar** (Sternchen plus Legende).
3. **Ein ungültiger Wert reißt die übrigen nicht mit.** Jede Einstellung wird
   einzeln geschrieben; was durchfällt, wird benannt, der Rest bleibt stehen.
   Nachgewiesen: eine ungültige Größenstufe wurde abgewiesen, während
   `search.max_results` im selben Durchgang ankam.

### ✅ Vier Rückmeldungen aus dem Praxistest

**1. Vorschaubilder luden ab Kachel 200 gar nicht mehr nach — echter Fehler.**
Die Ansicht bestimmte ihren sichtbaren Bereich durch Abtasten (`indexAt()` auf
den beiden Ecken des Sichtfensters). Sie legt ihre Kacheln aber stapelweise aus
(`LayoutMode.Batched`, `batchSize` 200), und ausserhalb des ausgelegten Stapels
trifft `indexAt()` nichts. Die untere Ecke lieferte **immer** einen ungültigen
Index, das Ende fiel auf den Ersatzwert 200 zurück — und wer weiter gescrollt
hatte, bekam einen Bereich, dessen Anfang hinter seinem Ende lag.
`items_in_range()` liefert dann eine leere Liste.

| Scrollposition | vorher | jetzt |
|---|---|---|
| ganz oben | Bilder 0..401 | 0..23 |
| Mitte | nur 18 Stück | 183..218 |
| 90 % | **nichts** | 339..374 |
| ganz unten | **nichts** | bis zum letzten Bild |

`request_visible()` **rechnet** den Bereich jetzt aus Rollbalkenstand und
Rastermaß, statt ihn abzutasten — alle Kacheln sind gleich groß
(`setUniformItemSizes`), also trifft die Rechnung immer. An beiden Anschlägen
wird bis zum Ende geklammert, weil die gerechnete Zeilenhöhe um wenige Pixel
von Qts Auslegung abweicht und sich das über tausend Zeilen summiert.
7 Tests in `tests/test_thumbnail_scrolling.py`.

**2. Prompt Evolution: die Meldungen logen.** Der Vorgang war in Ordnung, die
Auskunft nicht. Es gab **einen** Text für drei völlig verschiedene Fälle, und
der Rat „über Analyse berechnen" half nur bei einem davon:

| Fall | Bilder (echte Bibliothek) | Meldung jetzt |
|---|---|---|
| gar keine Promptdatei | **21.052** | „Zu diesem Bild gibt es keine Promptdatei" |
| Prompt, aber keine Familie | 2.067 | „gehört zu keiner Familie … 'Ähnliche Prompts finden'" |
| Familie ohne Baum | 4.739 | „… berechnen" — hier hilft es |

Drei Viertel des Bestands haben keine `.txt` daneben; der Rat zu rechnen konnte
dort nie helfen. Zusätzlich meldete ein zweiter Klick auf „Evolutionsbäume
berechnen" **„keine Evolutionsdaten"**, obwohl alle Bäume aktuell waren — das
klang nach Fehlschlag. Jetzt: „alle 353 Bäume sind aktuell"
(`EvolutionStats.up_to_date`). Die Testbibliothek enthält jetzt beide zuvor
fehlenden Fälle.

**3. Der Einstellungsdialog sah unaufgeräumt aus — berechtigt, und messbar.**

* Die **Reiterleiste brauchte 1404 px** bei 720 px Fensterbreite. Qt blendete
  Pfeiltasten ein, die Hälfte der Kategorien war unsichtbar. Jetzt eine
  **Kategorienliste links**, die nichts ausblendet.
* Die **Zahlenfelder waren 14 bis 108 px breit**, je nach Wertebereich —
  untereinander stand nichts. Jetzt einheitlich `FIELD_WIDTH`, mit fester
  Beschriftungsspalte, damit die Feldspalte beim Kategoriewechsel nicht springt.
* Die Reihenfolge war **alphabetisch nach Schlüssel** und riss Zusammen-
  gehöriges auseinander; jetzt gilt die Reihenfolge des Schemas.
* Die Fenstergröße folgt dem umfangreichsten Bereich statt geraten zu sein.

**4. Die Einfärbung im Editor war unerklärt und unveränderbar.**
Unter dem Editor sitzt jetzt eine Legende (`app/gui/editor/legend.py`) mit den
echten Palettenfarben — nicht mit eigenen Literalen, sonst zeigte sie im
Dunkelmodus etwas anderes als der Editor:

> **selten – macht diesen Prompt aus** · **Textbaustein – steht in vielen
> Prompts** · **Negativ-Prompt**

Dazu drei Farbregler unter *Anzeige*. Sie sind **je Schema** gespeichert,
sichtbar ist aber nur einer — der für das gerade aktive Schema. Ein einziger
gemeinsamer Wert wäre in einem der beiden unlesbar geworden. Der Wert liegt als
JSON `{"light": "#…", "dark": "#…"}` in einer Einstellung; `scheme_colors()`
prüft ihn, `with_overrides()` legt ihn über die Palette des aktiven Schemas.
Eine Änderung erscheint sofort (`ThemeManager.refresh()`), nicht erst nach
einem Neustart.

### ✅ Drei weitere Rückmeldungen

**5. Die Einfärbung sass mitten im Wort — echter Fehler, kein Einstellungsproblem.**
Gemeldet als: „Do NOT distort or" blau, „sty" farblos, „lize identity." grau.
Die Segmentgrenzen aus der Datenbank zählen im **Rohtext** der Datei, Qt zählt
im **Dokument**: `setPlainText()` wandelt jedes CRLF-Paar in einen einzelnen
Umbruch, das CR-Zeichen verschwindet. Bei Windows-Zeilenenden verschiebt sich
die Hervorhebung dadurch mit jeder vorangehenden Zeile um ein weiteres Zeichen
nach links.

Nachgemessen an Prompt 8 der echten Bibliothek: 580 Zeichen gespeichert, 571 im
Dokument, 9 CR verworfen — **von 11 Segmenten sassen 8 falsch**. Nach der
Umrechnung in `PromptEditor._translate()` sitzen alle 11 richtig.

Die Umrechnung liegt bewusst im Editor: nur er kennt beides, Rohtext und
Dokument. Die Alternative — den Text beim Einlesen normalisieren — hätte
`text_hash` und damit Dedup, Familien und Bäume der ganzen Bibliothek
verschoben.

**6. Das gelbe Kreuz auf den Kacheln ist entfernt.**
`_paint_missing_prompt` zeichnete zwei gekreuzte Linien in der Warnfarbe auf
jede Kachel ohne Promptdatei. Zwei Gründe dagegen: es sah aus wie ein
Schließknopf, und es war kein Ausnahmefall — **21.052 von 27.858 Bildern (75 %)**
haben keine Textdatei. Eine Warnung, die auf drei Viertel aller Kacheln steht,
warnt vor nichts. Ob ein Prompt vorliegt, sagt die Vorschau beim Anklicken.

**7. Fenstergröße und -position: war schon da, jetzt geprüft und abgesichert.**
`_restore_geometry()` und `closeEvent()` gab es bereits, und auf der echten
Plattform funktionierten sie auch — nachgemessen: 1100×700 an (150,100) kam
unverändert zurück, ebenso der maximierte Zustand und das Beenden über das
Menü. Geprüft war das nie.

Ergänzt wurde ein zweites Netz: `closeEvent` läuft nur, wenn das **Fenster**
geschlossen wird. Endet die Anwendung anders, greift jetzt `aboutToQuit` — sonst
wäre die Größe genau dann verloren, wenn man sie am ehesten vermisst.
`tests/test_window_geometry.py` hält alle Fälle fest.

### ✅ Paketierung (Teil 3) — über AUTOBUILD.py

Statt einer `.spec`-Datei ist dein eigenes Verfahren übernommen: **ein Aufruf,
und die .exe steht.**

```
python AUTOBUILD.py                # Version schreiben, bauen, Verknüpfung
python AUTOBUILD.py --version-only # nur build_version.py erneuern
```

| Baustein | Zweck |
|---|---|
| `AUTOBUILD.py` | Version bestimmen, `build_version.py` schreiben, PyInstaller aufrufen, Verknüpfung anlegen, aufräumen |
| `build_version.py` | erzeugt, nicht versioniert — Version, Bauzeitpunkt, Commit |
| `main.py` | Einstiegspunkt für PyInstaller (ein `-m app` lässt sich nicht paketieren) |
| `app/gui/resources.py` | findet mitgelieferte Dateien im Quelltext **und** im entpackten Paket (`sys._MEIPASS`) |

**Die Version steht in der Titelzeile** (`version_label()` in `app/__init__.py`)
und im Dateinamen der .exe: `PicPromptEditor_0.1.0.exe`.

Zwei Dinge, die Vorsicht verlangten:

* **PySide6 muss ausgeschlossen werden.** PyQt6 6.11 und PySide6 6.9.2 liegen
  nebeneinander und bringen gleichnamige Qt6-DLLs mit; sammelt PyInstaller
  beide ein, stirbt die .exe beim Import von QtCore. `EXCLUDED` in
  `AUTOBUILD.py` ist deshalb die wichtigste Zeile der Datei.
* **Ohne Git muss es auch gehen.** Dieses Projekt ist kein Repository;
  `get_version()` fällt dann auf `SOURCE_VERSION` aus `app/__init__.py` zurück.
  `SOURCE_VERSION` wird bewusst **nie** überschrieben — sonst schriebe jeder
  Bau die Version des vorigen fort (ein Fehler, der im ersten Anlauf genau so
  passierte: der Bau übernahm die Version eines fremden Projekts).

**Nachgewiesen:** die gebaute .exe (54,2 MB) startet und liest die echte
Bibliothek. `--check` meldet jetzt zusätzlich, ob die mitgelieferten Dateien
gefunden werden — im Paket: `…\_MEI000095542pp\gui`, vollständig. Ein
unvollständiges Paket fiele sonst erst beim Hinsehen auf.

Ein Installer (Inno Setup o. ä.) ist weiterhin nicht vorgesehen; für ein
Werkzeug auf einem Rechner genügt die .exe samt Verknüpfung.

### ✅ Fünf weitere Rückmeldungen

**8. Die Statusleiste zappelte.** Die Tätigkeitsanzeige nennt während eines
Abgleichs den gelesenen Ordner, und Ordnerpfade sind unterschiedlich lang —
jeder neue Pfad schob die Anzeigen rechts daneben hin und her. Sie hat jetzt
eine feste Breite (`ACTIVITY_WIDTH`); zu langer Text wird in der **Mitte**
gekürzt (bei einem Pfad sind Anfang und Ende aussagekräftig), der volle Pfad
steht im Tooltip.

**9. Das Promptsymbol ist zurück — als Symbol, nicht als Kreuz.**
`app/gui/assets/prompt.webp`, klein unten rechts in der Kachel, Größe abhängig
von der Kachelgröße (14–32 px). Die Bedeutung ist **umgekehrt** zu vorher: das
Symbol kennzeichnet, dass ein Prompt **vorhanden** ist — das ist mit 25 % die
Ausnahme und deshalb die kennzeichnungswürdige Seite.

Verkleinert wird einmal je Größe mit Glättung (aus 512 px ohne Glättung auf
18 px entstünde ein zerrissenes Bild), gezeichnet wird dann pixelgenau ohne
weitere Umrechnung — dadurch bleiben die Kanten scharf.

**10. Die Fensterbreite rastet auf ganze Kachelspalten ein.**
`MainWindow._snap_width()` verkleinert das Fenster nach jeder Größenänderung um
den Überstand. Gemessen wird am **Sichtfenster** des Kachelrasters, der
Rollbalken zählt also nicht mit und steht nie über der letzten Spalte.
Verkleinert wird nur, nie vergrößert. Gemessen auf dem 3440er Schirm: das
Fenster springt zwischen 1384 px (5 Spalten) und 1660 px (6 Spalten), Überstand
jeweils 0. Abschaltbar über `ui.snap_window_to_tiles`.

**11. Die Kachelansicht lässt sich sortieren.** Auswahlfeld über dem Raster,
sieben Ordnungen (Name auf/ab, Zeitstempel auf/ab, Dateigröße auf/ab, mit
Prompt zuerst). Sortiert wird im Modell, nicht in der Datenbank: die
Ergebnismenge liegt bereits vollständig vor, und ein neuer Datenbankdurchlauf
würde die eben erzeugten Vorschaubilder verwerfen. Die Wahl bleibt über den
nächsten Ordnerwechsel hinweg bestehen.

**12.** (siehe Punkt 5–7 oben — Einfärbung, Kreuz, Fenstergeometrie)

Geplant ist eine geprüfte `.spec`-Datei im Projekt; auto-py-to-exe kann sie
laden. **Die eine echte Gefahr** ist bereits bekannt: PyQt6 6.11 und PySide6
6.9.2 liegen nebeneinander und bringen gleichnamige Qt6-DLLs mit. Sammelt
PyInstaller PySide6 mit ein, startet die .exe nicht. Muss ausgeschlossen werden
(`--exclude-module PySide6 --exclude-module shiboken6`).

Erleichternd: das Projekt enthält **keinen einzigen dynamischen Import**, und
die Migrationen sind statisch eingebunden — die häufigste PyInstaller-Fehler-
quelle entfällt.

Ein echter Installer (Inno Setup o. ä.) ist **nicht** vorgesehen; PyInstaller
erzeugt eine .exe, und für ein Werkzeug auf einem Rechner genügt das.

---

### ✅ Nachbesserungen am Kachelraster

**13. Die Lücke rechts war ein Rechenfehler.** Das Einrasten liess einen
Streifen von fast einem Kachelviertel stehen. Ursache: `_grid_step()` rechnete
mit dem **doppelten** Abstand. Qt legt aber so aus:

```
Rand(spacing) | Kachel | spacing | Kachel | spacing | ...
```

Das Spaltenmass ist also Kachelbreite + **einmal** spacing — 272 px statt der
angenommenen 276. Ausgemessen an Qts tatsächlicher Auslegung: die Kacheln
stehen bei x = 4, 276, 548, 820, Abstand also 272.

Dazu kommt ein Randmass, das sich nur messen liess: n Spalten erscheinen ab
`n * 272 + 5`. Der zusätzliche Bildpunkt ist Qts Innenleben. Mit einer
Schrittweite von einem Bildpunkt über den ganzen Bereich abgetastet, ist die
Regel über alle Spaltenzahlen exakt. Ergebnis: angeforderte Spalten =
sichtbare Spalten, Lücke nur noch 5 px statt 92.

**14. Das Fenster flackerte beim Ziehen.** Die Korrektur lief bei *jedem*
Grössenänderungsereignis und sprang gegen die Mausbewegung zurück. Jetzt zwei
Massnahmen:

* `setSizeIncrement(272, 1)` — **der Fenstermanager selbst** rastet beim Ziehen
  in Kachelschritten ein. Nichts wird nachträglich zurückgesetzt, also flackert
  nichts. Wirksam nur beim Ziehen mit der Maus, nicht bei `resize()` aus dem
  Programm.
* Ein Zeitgeber (`SNAP_DELAY_MS` = 250 ms), der bei jeder Änderung neu startet
  und erst nach dem Loslassen feuert — als zweites Netz.

Nachgemessen: während zwölf schnell aufeinanderfolgender Grössenänderungen
**keine einzige Korrektur**, danach ein Sprung auf die exakte Breite.

**15. Bibliotheksweite Ansicht "Neueste Prompt-Paare".**
Menü *Bibliothek → Neueste Prompt-Paare in neuem Tab* (Strg+N). Zeigt nur
Bilder **mit** Prompt — die Frage lautet „was habe ich zuletzt erzeugt", und
Bilder ohne Textdatei (drei Viertel des Bestands) würden die Antwort
zuschütten. Sortiert nach dem Zeitstempel des **Prompts**, nicht des Bildes:
beim Kopieren bekommen Bilder oft neue Zeitstempel, Textdateien seltener.
In der echten Bibliothek: 6.806 Paare.

**16. Ergebnistabs haben das Sortierfeld mit** — sie nutzen dieselbe
`ExplorerPanel` wie das Bibliothekstab.

**Dabei einen selbst eingebauten Fehler bemerkt:** die Sortierung überschrieb
die Reihenfolge der Abfrage. Bei einer Ähnlichkeitssuche hätte das die Relevanz
zerstört, bei „Neueste Paare" die Zeitordnung. Die Vorgabe heisst jetzt
**„Wie geliefert"** und sortiert *nicht* um; das Modell merkt sich die
gelieferte Reihenfolge, sodass man jederzeit dorthin zurückkehren kann.

---

### Betriebsbeobachtungen (05.09.2026)

**17. Die Scanner-Warnung war schlicht falsch.** „N Prompt(s) in … gehoeren zu
mehreren Bildern" stand **345-mal** in der Problemliste. Ein Prompt an mehreren
Bildern ist in dieser Bibliothek aber der **Regelfall** — wer eine Bildserie aus
einem Prompt erzeugt, legt eine Textdatei und viele Bilder ab. Die Warnung gab
nichts zu tun und verdeckte die Meldungen, die etwas bedeuten. Entfernt; die
Zahl wird weiter gezählt und in der Scan-Zusammenfassung neutral genannt
(„4 Prompts mehrfach verwendet").

**18. Ein gescheiterter Analyselauf sah aus wie ein leeres Ergebnis.**
Das ist der eigentliche Schaden des einmaligen TypeError: `analysis_finished`
feuerte mit einer leeren Statistik, die Statusleiste meldete beruhigend „keine
Aenderungen", und der Fehler stand nur in der Problemliste. Neu:
`AnalysisService.analysis_failed(str)` und eine Meldung in der Statusleiste.

**Zum TypeError selbst — offen, aber eingegrenzt.**
`'function' object does not support the context manager protocol`, gemeldet an
`similarity.py:219` (`shared = self._weight(common)`). Geprüft und
ausgeschlossen:

* kein `with` in der genannten Funktion oder ihrem Aufrufweg;
* alle 49 `with self._lock` binden echte `threading.RLock`, keine Funktion;
* **kein veralteter Bytecode** — 107 `.pyc` geprüft, alle Zeitstempel stimmen
  (die Vermutung lag nahe, weil das Projekt auf einer Netzfreigabe liegt);
* der Traceback endet **am Aufruf** von `_weight`, ohne Rahmen für dessen
  Rumpf — zu keiner Stelle im Quelltext passend.

In vier vollständigen Analyseläufen seither nicht wieder aufgetreten. Falls er
wiederkehrt: der Lauf meldet jetzt Fehlschlag statt Erfolg, und `error_log`
behält den vollständigen Traceback.

**19. Vorschaubilder: nichts geht verloren.** Nachgemessen an der echten
Bibliothek: 8.559 Cachezeilen, 11.253 Dateien, 136 MB, Stichprobe 200 von 200
lesbar. Erst **2.853 von 27.858 Bildern** haben überhaupt eines — was nach
„weg" aussieht, ist meist ein noch nicht erzeugtes.

**20. Die NAS-Prüfung hat eine eigene Spur bekommen.** Sie lief im io-Pool,
gemeinsam mit Abgleich und Vorschaubildern (4 Fäden). Sind die mit Zugriffen auf
ein hängendes NAS belegt, kommt ausgerechnet die Prüfung nicht mehr dran, die
das feststellen soll — der Breaker wäre blind, wenn er gebraucht wird. Neuer
Pool `probe` mit einem Faden.

Zusätzlich gehen Vorschaubilder im io-Pool jetzt **vor** dem Abgleich
(`THUMBNAIL_PRIORITY`): sonst wartet man während eines Hintergrundscans auf
Kacheln, die man gerade ansieht. (Zur Einordnung: ein Abgleich dauert bei dieser
Bibliothek 13,5 s, ist also kein Dauerblocker.)

**Nicht reproduziert:** der vollständige Stillstand der Vorschaubilder. Ein
Durchlauf mit 50 Bildern, wiederholtem Öffnen und Schließen von Tabs und
mehrfacher Evolution zeigte keinen Rückstau (`_pending` blieb 0, Auslieferung
lief weiter). Die Vermutung „geschlossene Tabs hängen am dienstweiten Signal"
wurde geprüft und **widerlegt**: Qt trennt beim Zerstören selbst.

---

### Anpassungen an die Bibliotheksstruktur (05.09.2026)

**21. Das Promptsymbol ist innen weiß gefüllt.** Es war ein schwarzer
Seitenumriss auf Transparenz — auf dunklen Kacheln kaum zu erkennen. Die
Innenfläche wird jetzt geflutet und **unter** den Umriss gelegt (statt darüber),
damit die Kanten und die Textzeilen scharf bleiben.

**22. Der Ordnerbaum zeigt die Zahl aus `name/ai`.**
Nachgemessen: **99,6 % aller Prompts** (6.827 von 6.853) liegen in einem
Unterordner `ai`; 103 solche Ordner gibt es. Die Bildzahl des Elternordners
zählt dagegen Beiwerk:

```
Alyson Hannigan  (3481)     eigene Bilder:  345
Mandy             (835)     eigene Bilder:  225
Reese Witherspoon (555)     eigene Bilder:  540
```

`FolderNode` trägt dafür `ai_image_count` und `ai_prompt_count`; die Zahlen
kommen als Unterabfrage aus derselben Baumabfrage, nicht aus einem zweiten
Durchlauf. Hat ein Ordner keinen `ai`-Unterordner, gilt seine eigene Zahl —
sonst stünde ausgerechnet bei den `ai`-Ordnern selbst nichts. Der Tooltip nennt
beide Zahlen.

**23. „Inhalt mit Unterordnern anzeigen" ist ersetzt** durch **„In neuem Tab
öffnen"**. Der Eintrag öffnet denselben Ergebnistab wie „Neueste Prompt-Paare",
nur mit dem angeklickten Ordner — samt Kachelraster und Sortierfeld. Geladen
wird der Ordner **einschließlich Unterordner**, weil der Baum in Klammern die
`ai`-Zahl nennt: wer darauf klickt, will diese Bilder sehen und nicht die paar
Dateien daneben.

---

### Der Fehler hinter den leeren Kacheln (05.09.2026)

**24. Vorschaubilder erreichten nur die erste Ansicht.**
Gemeldet als: „beim Öffnen eines ai-Ordners in einem Tab sind die Kacheln da,
die Bilder nicht, und es werden auch keine Thumbs erzeugt."

`ImageService.request_thumbnails()` hielt ein Bild für erledigt, sobald es im
RAM-Cache lag — unter der Annahme, wer es anfordert, habe es ohnehin schon:

```python
if (item.thumbnail_key, self._display_size) in self._ram:
    continue          # <- verschluckt
```

Für eine **neu geöffnete** Ansicht stimmt diese Annahme nicht. Wer einen Ordner
ansah (RAM gefüllt) und ihn danach in einem Tab öffnete, bekam dort leere
Kacheln — und es wurde auch nichts nachgeholt, denn die Bilder galten ja als
vorhanden. Nachgestellt und bestätigt: Bibliothek 12 von 12 Kacheln mit Bild,
derselbe Ordner im Tab 0 von 12.

Jetzt wird aus dem RAM **sofort ausgeliefert** statt übersprungen — über
dasselbe Postfach wie frisch erzeugte Bilder, also an alle Ansichten und ohne
erneute Datei- oder NAS-Arbeit.

**Ein Test hielt genau das falsche Verhalten fest.**
`test_thumbnails_are_generated_and_cached` verlangte `assert not ready` —
zwischengespeicherte Bilder dürften nichts auslösen. Die Absicht (keine erneute
Dateiarbeit) war richtig, die Zusicherung falsch: sie schrieb das Verschlucken
fest. Jetzt wird beides geprüft — **keine neue Erzeugung, aber Auslieferung**.

Das erklärt sehr wahrscheinlich auch die früheren Beobachtungen „Thumbnails
sind nach dem Neustart weg" und „die Erzeugung tut nichts mehr": beide traten
nach dem Öffnen und Schließen von Tabs auf.

---

### Feinschliff (05.09.2026)

**25. Tabtitel tragen den Elternordner.** Alle interessanten Ordner heissen
`ai` — ein Tab mit diesem Titel war von jedem anderen ununterscheidbar. Jetzt
`Mandy/ai`. Ein Laufwerksbuchstabe zaehlt dabei nicht als Elternordner.

**26. Farbwechsel erreichte den Haupttab nicht.**
`_on_palette_changed` verteilte die neue Palette an Explorer, Statusleiste und
alle dynamischen Tabs — aber **nicht** an `self._preview`, den Prompt-Editor des
Bibliothekstabs. Eine geaenderte Hervorhebungsfarbe erschien dadurch ueberall,
nur nicht dort, wo man sie zuerst sucht.

**27. Die Kopfzeile nennt jetzt Bildmasse und Dateigroesse.**
`Prompt - bild.png | 1024 x 1536 px | 25 KB`, in jeder Ansicht mit dieser Zeile.
Die Masse stehen **nicht** in der Datenbank (`width`/`height` sind bei allen
27.923 Bildern 0 — der Scanner oeffnet die Dateien nicht, das waere ueber SMB zu
teuer). Sie werden aus dem ohnehin geladenen Original genommen; die Zeile
schreibt sich deshalb zweimal: Dateigroesse sofort, Masse sobald das Bild da ist.

**28. Der Evolutionsbaum zeigt endlich, WAS sich geaendert hat.**
Die Spalte sagte „2 ergaenzt", der Prompt darunter faerbte weiter nach
Haeufigkeit. Jetzt haben Aenderungen Vorrang: neue Segmente in `diff_added`,
geaenderte in `diff_changed` — dieselben Farben wie in der Vergleichsansicht.
Die Legende schaltet dabei um.

Moeglich wurde das, weil `segment_stats_for_prompt` bereits `token_id`
mitliefert; `SegmentSpan` traegt sie jetzt, und der Baum kennt `added`/`changed`
je Knoten. **Entfernte** Segmente lassen sich hier nicht zeigen — sie stehen im
Elternprompt, nicht in diesem; dafuer ist die Vergleichsansicht da.

**29a. Der eigene Schliessknopf riss die Anwendung mit (behoben).**
Ein Klick auf das X beendete das ganze Programm. Ursache: der Knopf loeschte
den Tab aus seiner **eigenen** Klick-Behandlung heraus, und Qt zerstoert dabei
genau diesen Knopf - die Anwendung starb kommentarlos. Das Schliessen laeuft
jetzt ueber `QTimer.singleShot(0, ...)`, also erst nachdem das Ereignis
abgearbeitet ist. Ein Test klickt den Knopf und besteht darauf, dass genau ein
Tab verschwindet und die uebrigen bleiben.

**29. Der Abstand des Schliesskreuzes ist einstellbar** (`ui.tab_close_margin`,
Vorgabe 4). Qt kennt von sich aus nur zwei Zustaende — nachgemessen: 13 px ohne
Formatvorlage, 1 px mit. Weder `margin` noch `margin-right` an
`QTabBar::close-button` haben eine Wirkung. Dazwischen kommt man nur ueber einen
eigenen Knopf in einem Behaelter mit Rand; der Abstand ist dann `margin + 1`.
Preis: die noetige `QTabBar::tab`-Formatvorlage zeichnet die Tabs etwas weniger
nativ.

**30. Pfadanzeige und Ladebalken sind getauscht.** Die Tatigkeitsanzeige mit
dem Ordnerpfad steht jetzt links vom Balken, der damit naeher bei den Zahlen
sitzt, zu denen er gehoert.

**Zu den beiden gemeldeten Protokolleintraegen:** beide sind **historisch**, kein
neues Auftreten. Der Scanner-Eintrag steht auf `zuletzt 05.09 00:02:09` — vor
der Entfernung der Warnung; der Zaehler 345 ist seither nicht gestiegen. Der
TypeError trat **einmal** auf (04.09 19:44:23, Anzahl 1). Beide bleiben stehen,
bis sie im Problemdialog auf „Als erledigt markieren" gesetzt werden; die Spalte
„Zuletzt" zeigt ihr Alter.

---

### Absturz und Vergleichsansicht (05.09.2026)

**31. Der eigene Schliessknopf ist wieder draussen — er beendete das Programm.**
Ein Klick auf das X schloss die ganze Anwendung, in der Entwicklungsumgebung wie
in der .exe. Meine erste Erklaerung (Loeschen aus der eigenen Klick-Behandlung)
war **falsch** — das Verzoegern half nicht.

Reproduzieren liess sich der Absturz **dreimal nicht**: nicht mit
`button.click()`, nicht mit `QTest.mouseClick`, nicht mit echter
Ereignisschleife und `exec()`. Nur ein echter Mausklick loeste ihn aus.

Konsequenz: der eigene Knopf ist entfernt, es gilt wieder Qts eigener, der ueber
`tabCloseRequested` laeuft. Der Abstand betraegt damit 1 px statt der frueheren
5 px — vier Bildpunkte sind keinen Absturz wert, den ich nicht erklaeren kann.
Ein Test besteht darauf, dass kein `QToolButton` in den TabManager
zurueckkehrt.

**32. Der Vergleich markierte an der falschen Stelle** — derselbe
Zeilenumbruch-Fehler wie im Prompt-Editor. Die Vergleichsansicht hat einen
eigenen Highlighter und bekam die Korrektur von gestern nie mit. Die Rechnung
liegt jetzt in `app/gui/common/offsets.py` und wird von beiden benutzt;
nachgemessen an einem Prompt mit Windows-Zeilenenden treffen alle fuenf
Abschnitte genau ihren Satz.

**33. Uebersichtsleiste ueber das ganze Dokument** (`overview_bar.py`), neben
jedem der beiden Editoren. Sie bildet den vollstaendigen Prompt auf ihre Hoehe
ab und traegt jeden Unterschied als farbigen Strich ein — in derselben Farbe wie
im Text. Dazu ein Rahmen fuer den sichtbaren Ausschnitt, wie bei einer Minimap;
ein Klick springt hin. **Gleiche** Abschnitte bleiben aussen vor: bei aehnlichen
Prompts waere die Leiste sonst durchgehend gefaerbt und ohne Aussage.

**Noch nicht umgesetzt:** das Einfuegen von Leerzeilen zum Ausrichten
ungleich langer Abschnitte, wie WinMerge es macht. Das braucht einen
zeilenweisen Abgleich mit eigener Ausrichtungsschicht — deutlich mehr als eine
Anzeigeaenderung. Synchrones Scrollen gab es bereits.

**34. Menuepunkt "Neueste Vorlagen"** (Strg+Umschalt+N). Gegenstueck zu
"Neueste Prompt-Paare": dort das laufende Ergebnis aus den ai-Ordnern, hier das
Ausgangsmaterial direkt in den Namensordnern. Blattordner ohne Unterordner
zaehlen nicht — sie sind selbst Ergebnisordner. In der echten Bibliothek:
13.301 Vorlagen von 27.927 Bildern.

---

### Ein selbst verursachter Rueckschritt (05.09.2026)

**35. `PromptEditor.set_change_tokens` war verschwunden.**
Jeder Klick auf einen Knoten im Evolutionsbaum warf `AttributeError` — acht Mal
in dreizehn Sekunden.

Ursache: beim Umbau der Zeichenpositionen auf den gemeinsamen Baustein
`app/gui/common/offsets.py` habe ich mit einer groben Textersetzung gearbeitet:

```python
start = t.index("    def _translate(self, spans")
ende  = t.index("    def set_palette_colors(")
t = t[:start] + neu + t[ende:]
```

Zwischen den beiden Marken stand `set_change_tokens` — und wurde stillschweigend
mitgeloescht. Ein Ausschneiden zwischen zwei Markierungen entfernt eben alles
dazwischen, auch das, woran man nicht denkt.

**Warum kein Test es fand:** die Tests der Aenderungsfarbe pruefen den
Highlighter **unmittelbar** und gingen an Editor und Vorschau vorbei. Die
Weiterreichung war ungeprueft. Jetzt ruft `TestDurchreichung` die ganze Kette
durch — Vorschau -> Editor -> Highlighter — und ein weiterer Test haelt fest,
dass jede Stufe die Methode ueberhaupt besitzt.

Zusaetzlich geprueft, ob beim selben Umbau mehr verlorenging: alle Methoden von
`PromptEditor`, `PreviewController`, `HighlightLegend` und `SegmentHighlighter`
sind vorhanden. Und am echten Baum nachgestellt: jeder der vier Knoten laesst
sich anklicken, die Aenderung wird markiert, kein Fehler gemeldet.

---

### Ausrichtung und Vergleichsmarkierung (05.09.2026)

**36. Die Vergleichsmarkierung faerbte die ganze Kachel orange** — das Bild war
nicht mehr zu sehen. Ursache war **kein Fuellbefehl**, sondern ein Leck:
`_paint_marker` setzte den Pinsel fuer die A/B-Plakette und stellte ihn nicht
zurueck. Der unmittelbar danach gezeichnete Kachelrahmen (`drawRect`) fuellte
mit diesem Pinsel die ganze Flaeche.

Jetzt vier **Eckwinkel** statt eines Rahmens, halbdurchsichtig
(`MARK_ALPHA = 170`), und alles in `painter.save()/restore()`. Die Bildmitte
bleibt frei; ein Test malt die Markierung auf ein weisses Bild und prueft, dass
die Mitte weiss bleibt und die Ecke nicht.

**37. Der Menuepunkt schaltet um.** Er hiess immer "Fuer Vergleich markieren",
auch wenn schon markiert war. Jetzt nennt er, was der Klick **tut**:
"Markierung entfernen", sobald das Bild markiert ist — in der Kachelansicht wie
in der Bildansicht.

**38. Zeilenweise Ausrichtung der Vergleichsansicht** (`app/analysis/alignment.py`).
Fehlt in A eine Zeile, die B hat, verrutschte ab dieser Stelle **alles** Weitere
gegeneinander. Jetzt werden auf der kuerzeren Seite Leerzeilen eingeschoben,
sodass Zeile neben Zeile steht:

```
0 | Gemeinsame Einleitung ueber Licht. | Gemeinsame Einleitung ueber Licht.
1 | Ein Satz nur in A.                 |                        <- Leerzeile
2 | Noch eine gemeinsame Zeile.        | Noch eine gemeinsame Zeile.
3 |                                    | Erster Satz nur in B.  <- Leerzeile
4 |                                    | Zweiter Satz nur in B. <- Leerzeile
5 | Gemeinsamer Schluss.               | Gemeinsamer Schluss.
```

Die Zuordnung arbeitet **unabhaengig vom Textinhalt** auf Zeilenlisten
(`SequenceMatcher` ueber die Zeilen), nicht auf Saetzen oder Segmenten. Bei
ungleich langen Bloecken — drei Zeilen gegen fuenf — werden beide untereinander
gestellt und die kuerzere Seite aufgefuellt; welche Zeile innerhalb des Blocks
zu welcher gehoert, entscheidet niemand, weil es dafuer keine verlaessliche
Grundlage gibt.

Die Datei kennt **kein Qt und keine Datenbank** und ist damit ohne Oberflaeche
pruefbar (11 Tests).

**Die Positionen brauchen jetzt zwei Umrechnungen hintereinander:** erst die
verworfenen Wagenrueckluf-Zeichen (`offsets.translate`), dann die
eingeschobenen Leerzeilen (`AlignedText.map_offset`). Nachgemessen an einem
Vergleich mit Windows-Zeilenenden: alle Markierungen treffen ihren Satz.

Die eingeschobenen Zeilen werden ueber **Extraauswahl mit
`FullWidthSelection`** gefaerbt — eine leere Zeile hat kein Zeichen, das sich
einfaerben liesse. Der Text bleibt dabei unberuehrt; ihn mit Leerzeichen
aufzufuellen haette beim Herauskopieren unsichtbaren Unrat hinterlassen.

---

### M9 - Vergleichsengine neu gebaut (05.09.2026)

Der Textvergleich war das schwaechste Stueck des Programms und ist jetzt sein am
gruendlichsten geprueftes. Ausloeser war eine Meldung aus dem Betrieb:

> "Anfangs sind die Zeilen auf gleicher Hoehe, nach ein bisschen Scrollen
> verschiebt sich ein Text immer weiter nach unten."

**Die Ursache waren zwei Fehler, die sich addierten.**

1. **Der Gleichlauf rechnete anteilig.** `scaled_position()` bildete
   `wert/maximum` der einen Seite auf das Maximum der anderen ab. Das stimmt bei
   0 % und bei 100 % exakt und dazwischen nicht - genau das gemeldete Bild.
2. **Der Zeilenumbruch war eingeschaltet.** Die alte Ausrichtung sorgte fuer
   gleich viele **logische** Zeilen. Bei Umbruch belegt dieselbe Zeile links vier
   und rechts sechs **Anzeigezeilen**. Damit waren die Dokumente unterschiedlich
   hoch, die Rollbalken hatten verschiedene Reichweiten - und erst dadurch wurde
   Fehler 1 sichtbar.

Dahinter lag ein **struktureller** Fehler: es gab **zwei unabhaengige
Vergleiche**, die nichts voneinander wussten. `analysis/diff.py` rechnete
segmentweise auf Datenbankspannen, `analysis/alignment.py` zeilenweise zur
Anzeige, und die Spannen wurden hinterher **zweimal** umgerechnet
(`offsets.translate` -> `AlignedText.map_offset`). Jede Aenderung an der
Ausrichtung konnte die Einfaerbung verschieben. Das ist der Grund, warum dieser
Bereich seit M5 immer wieder brach.

#### Neue Bausteine

| Baustein | Ort | Aufgabe |
|---|---|---|
| `textdiff/` | `app/analysis/textdiff/` | Der Vergleichskern. Qt-frei, datenbankfrei, farbfrei |
| `normalize.py` | darin | Text -> `SourceLine`, Vergleichsform nach Optionen |
| `sequence.py` | darin | Diff-Algorithmus hinter einer Abstraktion (austauschbar) |
| `aligner.py` | darin | Opcodes -> `AlignedRow`, aehnliche Zeilen im Block zuordnen |
| `intraline.py` | darin | Wort- und Zeichenunterschiede innerhalb einer Zeile |
| `blocks.py` | darin | zusammenhaengende Zeilen -> `DifferenceBlock` |
| `merge.py` | darin | Bloecke uebertragen, ausschliesslich auf Quellzeilen |
| `engine.py` | darin | `compare_texts()` - die Kette in einem Aufruf |
| `DiffPane` | `app/gui/compare/diff_pane.py` | Eine Seite: Text, Zeilennummern, Faerbung |
| `row_heights.py` | `app/gui/compare/` | Die reine Rechnung fuer gleiche Zeilenhoehen |
| `scroll_sync.py` | `app/gui/compare/` | Gleichlauf ohne Umrechnung |
| `PromptWriter` | `app/filesystem/prompt_writer.py` | Atomares Zurueckschreiben mit Konflikterkennung |

**Entfernt:** `app/analysis/alignment.py` samt `tests/test_alignment.py`. Es war
eine schwaechere Doppelimplementierung dessen, was `textdiff` jetzt macht - ohne
Aehnlichkeitszuordnung, ohne Intra-Line-Diff, ohne Bloecke.

#### Wie die Zeilen jetzt buendig bleiben

Drei Stufen, und **alle drei** sind noetig:

1. **Ausrichtung** - beide Seiten haben gleich viele Zeilen, fehlende sind
   Platzhalter.
2. **Hoehenausgleich** - die kuerzere Zeile bekommt ueber
   `QTextBlockFormat.setBottomMargin` genau die fehlende Pixelhoehe.
3. **Gleichlauf** - danach eine schlichte Wertuebernahme, ohne Rechnung.

Nachgemessen an zwei echten Prompts aus der Bibliothek (77 Zeilen):

```
                     vorher         nachher
Basishoehe Zeile 0   30 / 105 px    ausgeglichen
Rollbalken-Maximum   verschieden    530 / 530
Zeilen ausser Tritt  -              0 von 77
```

#### Der Editor wurde ausgetauscht: QPlainTextEdit -> QTextEdit

Das war keine Geschmacksfrage. **`QPlainTextEdit` zaehlt senkrecht in
Anzeigezeilen** - ein Abstand unter einer Zeile ist fuer seinen Rollbalken nicht
vorhanden, der ganze Hoehenausgleich waere unsichtbar geblieben. `QTextEdit`
zaehlt in **Pixeln**. Der Preis ist ein langsamerer Aufbau bei sehr grossen
Dokumenten; bei Prompts von einigen hundert Zeilen faellt das nicht ins Gewicht.

#### Vier Fehler, die beim Bauen ans Licht kamen

**1. `blockBoundingRect().height()` zaehlt Blockraender nicht mit.** Nur `top()`
verschiebt sich durch sie. Wer die Hoehe fuer den belegten Platz haelt, misst
nach dem Ausgleich dieselben Unterschiede wie davor und haelt ihn fuer
wirkungslos. Getrennt in `base_heights()` (ohne Raender, fuer die Rechnung) und
`effective_heights()` (aus den Oberkanten, fuer den Nachweis).

**2. `FullWidthSelection` verdeckt die Wortmarkierung.** Eine Extraauswahl wird
**ueber** den Zeichenformaten gezeichnet - und auch ueber einer spaeter
hinzugefuegten zweiten Extraauswahl. Die Rangfolge aus dem Auftrag liess sich so
gar nicht herstellen. Der Zeilenhintergrund wird jetzt in `paintEvent` selbst
gemalt, **bevor** Qt das Dokument zeichnet. Das loest den Platzhalter gleich mit:
eine Zeile ohne ein einziges Zeichen laesst sich ueber Zeichenformate nicht
einfaerben, ueber ein Rechteck sehr wohl.

**3. Das Zeilenende wurde beim Zurueckschreiben verdoppelt.** `_encode` ersetzte
den Zeilenvorschub durch das Zielzeilenende, ohne vorhandene CRLF zu
vereinheitlichen - aus CR LF wurde CR CR LF. Die Datei waere bei **jedem**
Speichern um ihre eigene Zeilenzahl gewachsen. `decode_bytes` vereinheitlicht die
Enden naemlich nicht; der Text kommt so aus der Datenbank, wie er in der Datei
stand.

**4. Ein Ausgleich vor dem ersten Layout ist ein stiller Fehlschlag.** Ohne
gesetzte Textbreite liefert `blockBoundingRect` lauter Nullen, alle Abstaende
werden null, und nichts weist darauf hin. Gefunden hat das ein Waechter-Test, der
prueft, ob die Testdaten ueberhaupt unterschiedlich umbrechen - ohne ihn waeren
zwei Nachweis-Tests **leer erfuellt** durchgelaufen.

#### Farben: WinMerge statt Gelb/Gruen/Rot

Sieben neue Tokens in `Palette`, hell und dunkel. **Abweichung von Spezifikation
01** (Abweichungsregister Nr. 30, ausdruecklicher Wunsch):

| Token | hell | Bedeutung |
|---|---|---|
| `diff_block` | `#EFCB05` | ganze geaenderte Zeile |
| `diff_word` | `#F1E2AD` | Wortunterschied darin |
| `diff_word_deleted` | `#FFAA82` | entfallener Teil darin |
| `diff_placeholder` | `#C0C0C0` | Zeile fehlt auf dieser Seite |
| `diff_ignored` | `#FBF2BF` | durch Option unerheblich |
| `diff_moved` | `#FFCC80` | verschobener Block |
| `diff_current` | `#F0A000` | der angesprungene Unterschied |

Links und rechts tragen **dieselbe** Blockfarbe. Ob etwas hinzukam oder wegfiel,
erkennt man am grauen Platzhalter der Gegenseite - nicht an Gruen und Rot.

#### Rueckschreiben in die Promptdatei

Der erste Weg, auf dem dieses Programm eine Datei der Bibliothek veraendert.
Reihenfolge unumstoesslich: **Datei zuerst, Datenbank danach, Neuanalyse
zuletzt.** Umgekehrt stuende in der Datenbank ein Text, den es auf der Platte
nicht gibt.

* **Atomar** - Nachbardatei im selben Verzeichnis, flush und fsync, dann
  `os.replace`. Bricht der Vorgang ab, steht der alte Inhalt vollstaendig da.
* **Konflikterkennung** gegen Groesse und mtime aus der Datenbank, mit derselben
  2-Sekunden-Toleranz wie der Scanner. Rueckfrage statt stillem Ueberschreiben.
* **Encoding und Zeilenenden bleiben erhalten.** Eine cp1252-Datei als UTF-8
  zurueckzuschreiben zerstoert jeden Umlaut.
* `parse_version = 0` stellt den Prompt zurueck in die Analyse-Warteschlange.
  Segmente und Aehnlichkeiten stammen aus dem alten Text - eine falsche Auskunft
  ist schlechter als keine.
* Abschaltbar ueber `compare.allow_write_back`.

Der Prompt-Editor selbst bleibt schreibgeschuetzt; geaendert wird ausschliesslich
ueber die Uebernahme eines Difference Blocks.

#### Neun neue Einstellungen (Gruppe "Vergleich")

`allow_write_back`, `word_wrap`, `intra_line_mode`, `ignore_case`,
`ignore_whitespace_changes`, `ignore_blank_lines`, `detect_moved`,
`similarity_threshold`, `algorithm`.

#### Neue Tests (114)

| Datei | Was geprueft wird |
|---|---|
| `tests/test_textdiff.py` | Die zehn Faelle des Auftrags, Optionen, beide Algorithmen, Verschiebungen, Leistung |
| `tests/test_compare_layout.py` | Zeilenhoehen, Gleichlauf, **gemalte Pixelfarben** |
| `tests/test_compare_merge.py` | Uebernahme auf Quellzeilen, atomares Schreiben, Konflikte, Encoding |
| `tests/test_compare.py` | erweitert um die Rueckschreibkette Datei -> Datenbank -> Neuanalyse |

Der wichtigste Einzelfall ist **TEST 7** - eine Einfuegung *mitten* in einem
Aenderungsblock. Daran scheitert jeder Vergleich, der Zeilen der Reihe nach
paart.

**Bewusst nicht umgesetzt:** Myers-Diff von Hand. Der Auftrag laesst "Myers
**oder** eine saubere Abstraktion" zu; es gibt jetzt zwei Verfahren hinter
`DiffStrategy` - `SequenceMatcher` (Vorgabe) und **Patience**, das bei den
wiederkehrenden Textbausteinen dieser Bibliothek (41 % teilen denselben Kopf)
konstruktionsbedingt keine mehrdeutigen Anker zulaesst.

---

### M10 - VS-Code-Aussehen und Speichern ohne Ueberschreiben (05.09.2026)

#### Speichern trifft nie mehr eine Vorlage

Die Uebernahme aus der Vergleichsansicht schrieb bis dahin in die Promptdatei
zurueck. Auf Wunsch schreibt sie jetzt **immer in eine neue, vom Benutzer
gewaehlte Datei**. Die Regel steht nicht als Rueckfrage im Ablauf, sondern als
Sperre in der Bauweise:

* `PromptService.save_async` ist **entfernt**, ersetzt durch
  `export_async(prompt, text, ziel)`. Es gibt keine Methode mehr, die in
  `prompt.full_path` schreibt. Ein Test prueft genau das - inklusive, dass
  `full_path` im Quelltext des Service ueberhaupt nicht mehr vorkommt.
* `PromptRepository.update_text` ist ebenfalls entfernt. Sie existierte nur
  fuer den In-Place-Weg; stehengeblieben waere sie eine Einladung.
* **Beide** Originalpfade werden abgelehnt, nicht nur einer. Wer den Text von A
  ueber die Datei von B speichert, vernichtet genauso eine Vorlage.
* Verglichen wird mit `app/core/paths.py: same_path()`. Die Datenbank haelt
  Pfade als `Z:/Ordner\Datei.txt` - gemischte Trennzeichen sind unter Windows
  normal -, der Dateidialog liefert Schraegstriche. Ein Vergleich mit `==`
  haette dieselbe Datei fuer zwei verschiedene gehalten und die Sperre still
  ausgehebelt.

Die Datenbank bleibt bei einer Uebernahme unberuehrt: die neue Datei ist eine
neue Datei, kein geaenderter Prompt. Liegt sie in einer Bibliothekswurzel, nimmt
der naechste Abgleich sie von selbst auf. Einstellung heisst jetzt
`compare.allow_export`.

#### Das Aussehen kommt aus VS Code "Modern"

Gewuenscht war der azurblaue Akzent aus VS Code, ohne Umschaltmoeglichkeit.

**Ein Befund vorweg, der die Aufgabe verschoben hat:** verlangt war zunaechst
`hc_black.json`. Diese Datei enthaelt **kein Blau**. Ihre elf Farben sind
Schwarz, Weiss, ein gruener Auswahlbalken und ein graues Leerzeichen-Symbol.
Das Azur `#0078D4` steht in `dark_modern.json` (`focusBorder`,
`tab.activeBorderTop`). Uebernommen wurden deshalb **Dark Modern** und
**Light Modern**.

**Kein Themesystem.** Die Werte stehen als Konstanten in `app/gui/theme.py`.
Damit entfaellt der ganze Apparat aus Lader, Include-Aufloesung, Rueckfallkette
und Auswahl im Einstellungsdialog - und zur Laufzeit wird keine Datei
geoeffnet, was eine Ausnahme im Architekturtest erspart. Die JSON-Dateien
bleiben unter `app/gui/assets/themes/theme-defaults/`: sie sind die Quelle der
Werte und der Ausgangspunkt, falls doch umschaltbar werden soll.

Neue Tokens in `Palette`: `accent`, `accent_hover`, `accent_text`, `window`,
`surface`, `border`, `text`, `input_background`, `input_border`,
`selection_background`, `selection_text`, `line_number`.

**Zwei Werte stehen in keinem der Themes** und sind hier gewaehlt:
`selection_background` (dunkel `#04395E` - `list.activeSelectionBackground`
fehlt in Dark Modern) und `warning`. Beides ist im Quelltext als solches
gekennzeichnet.

**Die diff_*-Tokens bleiben themeunabhaengig.** VS Code kennt fuer Unterschiede
nur Gruen/Rot, und die meisten Themes definieren die Schluessel gar nicht. Sie
zu uebernehmen haette die WinMerge-Darstellung aus M9 zerstoert.

#### Der Akzent ist Styling, keine Farbe

Der wichtigste Einzelpunkt: die Palette sagt, WELCHE Farben es gibt - nicht,
dass ein fokussiertes Feld einen blauen Rahmen bekommt. Fusion zeichnet von
sich aus nichts dergleichen, und die Anwendung setzte bis dahin **ueberhaupt
kein Stylesheet**. Neu ist deshalb `style_sheet(palette)` mit Regeln fuer
Fokusrahmen, Akzentlinie ueber dem aktiven Tab, Auswahl in Listen und Baeumen,
Knoepfe, Leisten und Menues.

Ein Test haelt fest, dass darin **keine Literalfarbe** steht: jeder Hexwert muss
aus der Palette stammen.

#### Drei Fehler, die dabei ans Licht kamen

**1. `style().objectName()` ist unter einem Stylesheet leer.** Qt schiebt einen
Proxy davor, und PyQt6 gibt dessen Basisstil nicht heraus. Der Wachposten in
`apply_application_style` fragte genau das ab - er haette den Stil bei **jedem**
Aufruf neu gesetzt und damit die Schemawahl verworfen, also genau das
zerstoert, was er schuetzen soll. Gemerkt wird jetzt in `_ACTIVE_STYLE`.

**2. Das Hauptfenster stylte die Tabs selbst.** `QTabBar::tab { padding: ... }`
stand als Widget-Stylesheet in `main_window.py`. Ein Stylesheet am Widget
verdraengt fuer diese Eigenschaften das globale - die Akzentlinie waere dort
verlorengegangen. Das Innenmass steht jetzt bei allen anderen Tabregeln.

**3. Der Dunkelmodus war unter "offscreen" nie geprueft.** Dort liefert
`standardPalette()` fuer beide Schemata dieselben hellen Farben, weshalb **acht
von zwoelf** Tests des Farbschemas uebersprungen wurden. Seit die QPalette
ausdruecklich aus den Tokens gefuellt wird, laufen sie: von zehn
uebersprungenen Tests der Gesamtsuite sind noch **drei** uebrig.

#### Nachbesserung: eine Flaeche, Rahmen als Struktur

Der erste Wurf sah dem Vorbild nicht aehnlich. Der Grund war benennbar: vier
Grautoene, die sich um wenige Prozent unterschieden (`#181818` Leiste, `#1F1F1F`
Flaechen, `#202020` Kacheln, `#313131` Eingaben), und Rahmen in `#2B2B2B`, die
auf dunklem Grund praktisch unsichtbar waren. Das Ergebnis war weder flach noch
gegliedert.

VS Code staffelt seine Flaechen ebenfalls - aber dort tragen sichtbare Rahmen
die Gliederung. Uebernommen wurde deshalb das Prinzip, nicht die Zahlen:

* **Alle Flaechen tragen die Farbe der obersten Leiste.** `surface`,
  `tile_background` und `input_background` sind gleich `window`. Ein Test haelt
  das fest.
* **Jedes Bedienelement bekommt einen Akzentrahmen** - Eingaben, Auswahlfelder,
  Baeume, Listen, Tabellen, Knoepfe. Zwei Staerken desselben Blaus:
  `accent_dim` im Ruhezustand, `accent` beim Fokus. Ein Wechsel der FARBE, nicht
  der Breite - Letzteres verschoebe beim Hineinklicken das Layout um einen Pixel.

Dabei fiel eine Falle auf, die stumm geblieben waere: `QLineEdit,
QTextEdit:focus` gilt in CSS nur fuer die **letzte** Klasse. Ein Test prueft
jetzt, dass in jeder Zustandsregel jede Klasse ihren Zustand einzeln traegt.

#### Nachbesserung 2: Rollbalken, Pfeile, einstellbare Linienfarbe

Aus dem Betrieb gemeldet (x2.jpg): Rollbalken noch grau, Zahlenfelder **ohne
Pfeile**, Ankreuzfelder und Legendenfelder ohne Rahmen.

**Die Regel, die zweimal Pfeile gekostet hat.** Nachgemessen an Qt 6.11:
bekommt ein Bedienelement einen eigenen Rahmen im Stylesheet, hoert Qt auf,
seine Pfeilspitzen selbst zu zeichnen - uebrig bleibt eine leere Flaeche. Genau
so standen im Einstellungsdialog sieben Zahlenfelder da. Zwei naheliegende
Auswege scheiterten in der Probe:

* Der CSS-Kniff mit drei durchsichtigen Raendern ergibt in Qt **keinen Pfeil,
  sondern einen ausgefuellten Block**.
* Die Rahmenfarbe ueber die QPalette zu setzen geht nicht: `Mid`, `Dark`,
  `Shadow` und `Midlight` aendern den Rahmen eines Zahlenfeldes nicht.

Uebrig blieb der dritte Weg: acht kleine Pfeil-SVGs unter
`app/gui/assets/icons/`, eingebunden ueber `image: url(...)`. Zwei Fallstricke
dabei - `url()` braucht auch unter Windows Schraegstriche, und die Fassung
haengt am Farbschema, nicht am Akzent: ein Pfeil ist keine Linie, er soll wie
Text lesbar sein. Ein Test besteht darauf, dass **jede** Regel mit `-arrow` ein
Bild mitfuehrt.

Zweimal habe ich dabei einen niedrig aufgeloesten Screenshot falsch gelesen und
die Pfeile fuer ausserhalb des Rahmens gehalten. Nachgemessen: der Rahmen liegt
bei x=584 und x=733, das Feld reicht von 584 bis 733 - sie sind innen. Eine
Messung schlaegt einen Augenschein.

**Einstellbar sind jetzt die Linienfarben.** `ui.color_accent` und
`ui.color_accent_dim`, je Farbschema gespeichert, ueber denselben Mechanismus
wie die Segmentfarben (`OVERRIDABLE`). Dafuer nimmt `apply_application_style`
jetzt eine Palette entgegen: vorher baute das Stylesheet immer aus den
Konstanten, und eine gewaehlte Farbe waere auf allem wirkungslos geblieben, was
Qt selbst zeichnet - also gerade auf den Rahmen.

Ausserdem: die Legendenfelder holen ihren Rahmen aus der Palette statt aus
einem festen `rgba(128,128,128,0.5)`.

#### Hintergrundfarbe einstellbar, Evolutionsbaum uebersichtlich

**Die Flaechenfarbe des Programms** laesst sich jetzt einstellen
(`ui.color_background`, je Farbschema). Zwei Dinge waren dafuer noetig:

*Eine Einstellung, vier Felder.* Alle Flaechen tragen dieselbe Farbe; ein
Eintrag, der nur `window` setzt, faerbte die Leisten um und liesse Editor,
Kachelraster und Eingabefelder auf dem alten Wert - genau der Flickenteppich,
den die eine Flaechenfarbe beseitigt hat. `OVERRIDABLE` bildet deshalb jetzt
auf **mehrere** Palettenfelder ab.

*Zwei Ableitungen, damit eine freie Wahl das Programm nicht unbrauchbar
macht.* Der Platzhalter hebt sich immer um dieselbe Kleinigkeit ab - auf
dunklem Grund heller, auf hellem dunkler. Und **die Schrift folgt der
HELLIGKEIT des Hintergrunds, nicht dem Schema**: wer im Dunkelmodus Weiss
waehlt, bekaeme sonst hellgraue Schrift auf Weiss, und die Ursache waere nicht
zu erraten. Eine ausdrueckliche Wahl schlaegt die Ableitung immer.

**Der Evolutionsbaum zeigt keine Vorschaubilder mehr in den Zeilen.** Ein
Symbol von 64 Bildpunkten vervierfacht die Zeilenhoehe; bei fuenfzig Knoten
passt nichts mehr auf den Schirm, und die Verzweigung - worum es hier geht -
ist nicht mehr zu ueberblicken. Gemessen an der echten Bibliothek: **16 px
statt 64+** je Zeile.

Das Bild erscheint statt dessen beim Ueberfahren in einem kleinen Fenster
neben dem Zeiger. Kein Werkzeugtipp: ein QToolTip zeigt Text, ein Bild aus dem
Speicher bekommt man dort nur ueber eine Datei oder eingebettetes HTML hinein.

Dabei fiel auf, dass die Promptspalte fest bei hundert Bildpunkten stand - ab
der zweiten Baumebene war die Beschriftung auf "unv..." gekuerzt, und gerade
die tiefen Knoten sind die interessanten. Sie streckt sich jetzt ueber den
freien Platz, die drei Zahlenspalten nehmen nur, was sie brauchen.

**Ueber dem Baum steht ein Auswahlfeld mit den Familien.** Ohne das fuehrte der
einzige Weg zu einer anderen Familie ueber ein Bild aus ihr - man musste also
schon wissen, wo man hinwill, um hinzukommen. Angeboten werden nur Familien
mit **fertigem** Baum (`available_clusters`): eine Auswahl, die ins Leere
fuehrt, ist schlimmer als keine. Eine Sperre verhindert, dass schon das
Fuellen der Liste einen anderen Baum laedt.

**Das Programmsymbol** ist `PPEicon.ico`.

#### Duenne Linien statt Balken, und das Einrasten wieder gerade

Aus dem Betrieb gemeldet (x3.jpg), Vorbild war vscode.png.

**Das Einrasten der Fensterbreite war um ein paar Bildpunkte daneben.** Die
Ursache lag tiefer als der Stil: erscheint der senkrechte Rollbalken erst beim
Ueberlaufen, springt die Breite des Sichtfensters um seine Staerke - das
Grundmass fuer das Einrasten wurde aber vorher berechnet. Der Fehler war schon
immer da; das Stylesheet hat die Leiste von 14 auf 16 Bildpunkte verbreitert
und ihn damit sichtbar gemacht.

Der Rollbalken bleibt jetzt **immer** stehen. Eine feste Reserve macht den
Fehler unmoeglich, statt ihn kleiner zu machen.

**Was keinen Rahmen hatte, hat jetzt einen.** Vier Stellen:

| Stelle | warum sie durchgerutscht ist |
|---|---|
| Bildansicht | ist ein `QGraphicsView` - von keiner der bisherigen Regeln erfasst |
| Programmrahmen | das Tab-Widget laeuft im Dokumentmodus; dort zeichnet Qt grundsaetzlich keinen Rahmen um den Inhalt. Er haengt jetzt an einem benannten Behaelter (`#fensterRahmen`) |
| Tableiste | dieselbe Ursache - die Trennlinie sitzt jetzt an der Leiste selbst |
| Dateiname und Bildmasse | die Kopfzeile stand frei im Schwarzen, waehrend der Text darunter umrandet war. Kopfzeile, Text und Legende sind jetzt EINE Flaeche (`#editorPanel`) |

**Der Trenner war zu dick.** Qts Vorgabe von fuenf bis sechs Bildpunkten wirkt
blau eingefaerbt wie ein Balken. `SPLITTER_WIDTH = 3` - schmal genug fuer eine
Linie, breit genug, um sie noch zu treffen.

**Der Trenner eines Kacheltabs rastet jetzt ebenfalls auf ganze Kachelspalten
ein** (`app/gui/library/grid_snap.py`). Der Weg ist ein anderer als beim
Fenster: dort uebernimmt der Fenstermanager das ueber `setSizeIncrement`, ein
QSplitter kennt nichts dergleichen und muss nachtraeglich korrigiert werden -
mit Zeitgeber, sonst springt der Trenner unter dem Mauszeiger. Die Rechnung
steht als reine Funktion daneben und ist ohne Fenster pruefbar.

**Das Programmsymbol** ist `app/gui/assets/PPEicon.ico`, gesetzt auf der ganzen
Anwendung (sonst tragen Dialoge weiter das Qt-Zeichen) und in AUTOBUILD als
Symbol der .exe. Ein Test besteht darauf, dass beide dieselbe Datei nehmen.

#### Der Fehler, der sich nicht abhaken liess

Gemeldet: der oberste Eintrag der Fehlerliste kam nach jedem "Als erledigt
markieren" wieder, und im Protokoll lief ein `IntegrityError` auf. **Zwei
unabhaengige Fehler mit demselben Symptom.**

**1. Die Eindeutigkeitsbedingung.** Der Schluessel von `error_log` ist
(severity, source, message_key, **resolved**), und `resolved` gehoert
absichtlich dazu: nach dem Abhaken soll ein erneutes Auftreten eine neue offene
Zeile bekommen und nicht die alte wiederbeleben. Genau daran scheitert ein
blosses `SET resolved = 1` beim ZWEITEN Abhaken derselben Fehlerart:

```
1. Fehler tritt auf        -> Zeile (X, resolved=0)
2. Benutzer hakt ab        -> Zeile (X, resolved=1)
3. Fehler tritt erneut auf -> neue Zeile (X, resolved=0)
4. Benutzer hakt ab        -> Kollision mit der Zeile aus Schritt 2
```

`resolve()` fuehrt jetzt zusammen: gibt es die abgehakte Zwillingszeile schon,
wandern Anzahl und Zeitpunkt dorthin und die offene Zeile faellt weg. Dasselbe
gilt fuer `resolve_source` - dort war es schlimmer, weil ein gemeinsames UPDATE
bei EINER Kollision vollstaendig wirkungslos geblieben waere, nicht nur fuer
die eine Zeile.

**2. Read-after-write.** Der Dialog liest die Liste unmittelbar nach dem
Abhaken neu ein, das Schreiben lief aber nebenher. Er las den Stand VOR dem
Commit - der Eintrag stand wieder da, auch wenn alles funktioniert hatte.
`resolve()` wartet jetzt per Vorgabe auf seinen eigenen Schreibvorgang.

**Nachgesehen in der echten Datenbank**, und die Zahlen passen zur Meldung:

```
id=5  unhandled:AttributeError          x1   <- hat einen abgehakten Zwilling
id=7  database.writer:IntegrityError    x8   <- die Fehlschlaege selbst
```

Achtmal versucht, achtmal ein neuer Fehler. Der Versuch, den Eintrag
loszuwerden, war die Quelle des naechsten Eintrags.

**Die Tests wurden gegengeprueft:** gegen die alte Fassung fallen sieben von
acht durch. Ein Test, der vorher wie nachher gruen ist, haette nichts bewiesen.

#### Der Abgleich brach ab, obwohl nichts haengte

Ebenfalls gemeldet: dreimal `TimeoutError` in `folders.upsert`. Die Ursache war
kein haengender Schreiber, sondern eine **feste Frist als falsches Mass**. Der
DbWriter ist ein einzelner Faden mit einer Warteschlange; ein grosser Vorgang -
das Ersetzen aller Familien darf zehn Minuten dauern - haelt alles dahinter
auf. Wer mit `result(timeout=60)` wartet, bricht dann ab, obwohl der Vorgang
gleich danach erfolgreich durchlaeuft.

Schlimmer: eine abgelaufene Frist **storniert nichts**. Die Aufgabe blieb in der
Warteschlange und wurde ausgefuehrt - der Lauf war abgebrochen, die Daten aber
geschrieben.

Neu ist `DbWriter.await_result`: es begrenzt nicht die Gesamtdauer, sondern den
**Stillstand**.

**Dafuer braucht es drei Lebenszeichen, nicht eines.** Die ersten beiden
Anlaeufe waren jeweils zu kurz gesprungen:

| Lebenszeichen | deckt ab | reichte nicht, weil |
|---|---|---|
| `stats["tasks"]` | committete Aufgaben | waechst erst NACH dem COMMIT eines ganzen Stapels |
| `_progress` | jede einzelne fertige Aufgabe | steht still, solange EINE Aufgabe laeuft |
| `is_working` | der Schreiber steckt in einem Stapel | - |

Der dritte war der entscheidende. `cluster_repo.replace_all` leert vier
Tabellen und schreibt ueber hunderttausend Zeilen **in einer einzigen
Aufgabe**; ihre eigene Frist steht auf 600 Sekunden. Minutenlang wird dabei
nichts "fertig", obwohl der Schreiber durchgehend arbeitet - und alles, was
dahinter wartete, gab nach sechzig Sekunden auf. Genau dieser Abbruch wurde
nach dem ersten Anlauf erneut gemeldet.

`is_working` gilt von BEGIN bis COMMIT. Dazu eine absolute Obergrenze
(`MAX_WAIT`, 900 s) fuer den Fall, dass eine Aufgabe nur noch VORGIBT zu
arbeiten - sonst waere ein echter Haenger nicht mehr von Arbeit zu
unterscheiden.

Umgestellt sind alle Wartestellen in `folder_repo`, `image_repo` und
`prompt_repo`; ein Test besteht darauf, dass dort kein `.result(timeout=)` mehr
auftaucht.

**Gegengeprueft:** ohne `is_working` faellt
`test_eine_einzelne_lange_aufgabe_ist_kein_stillstand` mit demselben
`TimeoutError` durch, der gemeldet wurde.

#### Das Kachelsymbol kommt aus dem Iconset

Statt eines WebP aus 512 Bildpunkten, das fuer jede Kachelgroesse
heruntergerechnet wurde, jetzt das `document`-SVG aus dem mitgelieferten
Iconset - **in der Zielgroesse gerendert**. Bei achtzehn Bildpunkten ist das
ein sichtbarer Unterschied.

Das Set liefert von jedem Symbol eine helle und eine dunkle Fassung; die
Auswahl trifft das Farbschema, umgefaerbt wird nichts. Damit muss die Datei
nicht als Text eingelesen werden - Qt laedt sie selbst, wie zuvor die
Bilddatei. Zwischengespeichert wird in `app/gui/icons.py` je Groesse und
Schema; ein Themewechsel greift einfach auf einen anderen Eintrag zu.

`app/gui/assets/prompt.webp` wird dadurch nicht mehr benutzt. Die Datei ist
absichtlich liegengeblieben - sie ist Bildmaterial des Projekts, kein toter
Code. `prompt.ico` bleibt das Symbol der .exe.

#### Neue Tests (22)

| Bereich | Was geprueft wird |
|---|---|
| `tests/test_compare.py` | Es GIBT keine Methode, die das Original schreibt; beide Vorlagen gesperrt; gemischte Trennzeichen taeuschen die Pruefung nicht |
| `tests/test_theme.py` | Akzentwerte, Fokusregel, Akzentlinie, keine Literalfarbe im Stylesheet, Palette kommt bei Qt an, Stil wird nicht neu erzeugt |
| `tests/test_explorer_features.py` | Symbol in der Zielgroesse gerendert, hell und dunkel verschiedene Dateien, fehlendes Symbol bricht das Zeichnen nicht ab |

---

### Der eingefrorene Schreibweg (06.09.2026)

Zwei Rueckmeldungen, ein gemeinsamer Kern: **aus dem GUI-Faden wurde mit einer
Uhr auf den einen Schreibfaden gewartet.**

#### "Die Hintergrundfarbe wird nicht gespeichert"

Sie war gespeichert. In der Datenbank stand `ui.color_background` mit Wert und
Zeitstempel. Nur getragen hat sie beim Start niemand: `app/__main__.py` rief
`apply_application_style(preference)` **ohne Palette**, und der Aufruf baute
deshalb aus den Konstanten `_DARK` / `_LIGHT`. Die eingestellte Farbe erschien
erst, wenn der Einstellungsdialog geoeffnet und mit OK geschlossen wurde -
denn nur dort lief `ThemeManager.refresh()`. Fuer den Benutzer nicht von
"nicht gespeichert" zu unterscheiden.

`apply_application_style` nimmt jetzt `settings` entgegen und legt dieselben
Ueberschreibungen auf, die der ThemeManager spaeter aufloest. Nachgemessen an
der echten Bibliothek: vorher `#181818`, nachher `#000000`.

#### Der Absturz beim OK - und was er mitriss

```
TimeoutError
  settings_dialog.apply -> settings_service.set -> settings_repo.upsert
  -> Future.result(timeout=15.0)
```

`SettingsRepository.upsert` wartete mit **fester Frist** auf den EINEN
Schreibfaden. Steckt dort ein groesserer Vorgang, ist die Frist abgelaufen,
ohne dass irgendetwas haengt - im Betriebsprotokoll lief die Aufgabe 70
Sekunden nach dem Klick durch. Ein Zeitablauf **storniert die Aufgabe nicht
einmal**; dieselbe Erkenntnis hatte den Scanpfad schon einmal getroffen
(`await_result` statt `result(timeout=)`).

Schlimmer als der Absturz war die Folge: `SettingsDialog.apply` schreibt
Einstellung fuer Einstellung und fing nur `ValidationError` und `ValueError`
ab. Der `TimeoutError` verliess die Schleife, und **alles, was in der
Aufzaehlung dahinter stand, wurde nie geschrieben** - die Hintergrundfarbe
steht hinter den Akzentfarben. Damit ist auch die zweite Haelfte der ersten
Meldung erklaert.

* `settings_repo.upsert` schreibt ohne zu warten. Gefahrlos, weil der
  SettingsService den Wert typisiert im Speicher haelt, die Warteschlange
  strikt FIFO ist und `Application.shutdown` sie leert.
* `upsert_many` / `delete_unknown` warten ueber `await_result` - das misst
  Stillstand statt Gesamtdauer.
* `SettingsDialog.apply` faengt **jede** Ausnahme je Einstellung ab. Die
  Zusicherung des Docstrings galt bis dahin nur fuer zwei Fehlerarten.

#### "Als erledigt markieren" haengt das ganze Programm auf

`ErrorRepository.resolve` wartete ueber `DbWriter.await_result`. Das gibt
absichtlich erst auf, wenn der Schreibfaden **stillsteht** - solange er
vorankommt oder in einem Stapel steckt, wartet es bis `MAX_WAIT`, also eine
Viertelstunde. Im Hintergrund ist das genau richtig, im GUI-Faden ist es ein
Fenster, das auf nichts mehr reagiert.

`SystemService.resolve_problem` hat jetzt `wait`, und die Problemliste nutzt
`wait=False`: die Zeile verschwindet sofort, die Aufgabe steht in der
Warteschlange. Die Liste neu einzulesen waere der Fehler gewesen - sie liest
ueber eine Leseverbindung und saehe den Eintrag vor dem Commit wieder.

#### Die Rueckkopplung, die den Schreibfaden beschaeftigt haelt

Beim Nachsehen im Protokoll aufgefallen (03:27 Uhr, acht Mal): scheitert eine
**error_log**-Aufgabe, meldet der DbWriter den Fehlschlag, die Meldung reiht
ueber `DatabaseSink` die naechste error_log-Aufgabe ein, die aus demselben
Grund scheitert. Ein Kreis, der sich nicht von selbst oeffnet - und jeder
andere Schreibvorgang wartet dahinter. `DatabaseSink` erkennt diesen einen
Fall jetzt an Quelle **und** Aufgabenname und laesst ihn aus. Im Logfile
landet er weiter; der LoggingSink haengt am selben Reporter.

#### Neue Tests (13)

| Bereich | Was geprueft wird |
|---|---|
| `tests/test_theme.py` | Der Startaufruf traegt die eingestellte Farbe; ohne `settings` bleibt es bei den Konstanten; Startaufruf und ThemeManager ergeben dasselbe Stylesheet |
| `tests/test_config.py` | `set` wartet nicht auf einen blockierten Schreiber; der Wert landet trotzdem; eine spaetere Einstellung geht nicht verloren; keine feste Frist mehr im Quelltext |
| `tests/test_settings_dialog.py` | Ein werfendes `set` stoppt die Schleife nicht; `apply` wirft nicht nach aussen |
| `tests/test_database.py` | `resolve(wait=False)` kehrt sofort zurueck und verliert nichts; die error_log-Rueckkopplung ist unterbrochen, andere Schreibfehler werden weiter protokolliert |

---

### Durchsehen und Politur (06.09.2026)

Das ganze Projekt einmal auf Artefakte, Reste und halbe Sachen abgesucht. Was
gefunden wurde, in der Reihenfolge des Gewichts.

#### Fuenfzehn Regler ohne Wirkung

Von den **69 Einstellungen der Registry werden 15 nirgends gelesen**. Sie
stehen im Einstellungsdialog, lassen sich verstellen und tun nichts. Das ist
schlimmer als ein fehlender Regler, weil der Benutzer sich auf die Wirkung
verlaesst.

| Einstellung | Anmerkung |
|---|---|
| `library.follow_subfolders` | der Scanner steigt immer ab |
| `index.hash_images`, `index.hash_partial_bytes` | Abweichung Nr. 9 laesst den Teilhash zu, gebaut ist er nicht |
| `index.auto_reindex_on_schema_change` | **baulich nicht erfuellbar** |
| `thumbnails.max_cache_mb` | siehe unten |
| `thumbnails.preload_rows` | das Kachelraster laedt nur den Sichtbereich |
| `cache.ram_prompts`, `cache.cleanup_interval_s` | der Promptcache ist unbegrenzt |
| `search.fts_prefix_matching` | FTS5 sucht immer exakt |
| `analysis.evolution_auto_rebuild` | Baeume werden nur auf Anforderung gebaut |
| `parsing.index_words` | Wortsegmente gibt es nicht |
| `ui.restore_session` | offene Tabs werden nicht wiederhergestellt |
| `system.log_level` | **baulich nicht erfuellbar** |
| `system.debug_mode` | keine Zusatzdiagnose vorhanden |
| `system.backup_on_migration` | **baulich nicht erfuellbar** - gesichert wird immer |

Die drei mit "baulich nicht erfuellbar" werden gebraucht, **bevor** die
Datenbank offen ist, in der sie stehen: Logging und Migration laufen vor
`SettingsService.load()`. Sie gehoeren in die `bootstrap.json` (wo `log_level`
schon steht und auch benutzt wird) oder gestrichen.

Zwei weitere liessen sich sofort anschliessen, weil der Baustein dahinter schon
existierte und nur nie gerufen wurde:

* **`jobs.db_write_batch`** - `DbWriter.configure()` ist neu. Noetig, weil der
  Schreiber vor den Einstellungen entsteht; sie liegen ja in der Datenbank, in
  die er schreibt.
* **`system.error_log_max_rows`** - `ErrorRepository.prune()` gab es seit M0,
  gerufen hat es niemand. Steht jetzt in `startup()`.

`tests/test_architecture.py` fuehrt die Liste der verbleibenden fuenfzehn als
**Schuldliste**: eine neue Einstellung ohne Leser faellt auf, und eine
angeschlossene muss dort gestrichen werden, sonst schlaegt der zweite Test an.

#### Verwaiste Zeilen in `settings`

`SettingsRepository.delete_unknown` gab es die ganze Zeit und wurde nie
gerufen. In der gewachsenen Installation standen deshalb drei Leichen:
`ui.language` (i18n in M8 gestrichen), `compare.allow_write_back` (die
Uebernahme schreibt seit M10 nie mehr in die Vorlage) und
`ui.tab_close_margin`.

`SettingsService.load()` raeumt sie jetzt weg - **ausser beim Herabstufen**.
Stammt die Tabelle aus einer neueren Programmfassung, sind unbekannte
Schluessel keine Leichen, sondern Einstellungen, die diese Fassung noch nicht
kennt; sie zu loeschen hiesse, dem Benutzer beim Zurueckwechseln seine
Konfiguration zu zerstoeren. Dieselbe Regel galt in `_migrate` schon.

#### Zwei Abbrueche, die niemand ausloesen konnte

`AnalysisService.cancel_analysis` gibt es seit M4, `EvolutionService.cancel_build`
seit M6. Im Menue stand nur "Analyse abbrechen" - und das traf allein das
Zerlegen. Ausgerechnet die beiden Laeufe, die auf 7.000 Prompts Minuten dauern,
liessen sich nicht anhalten.

Jetzt hat jeder Lauf seinen eigenen Abbruch, und der Name sagt welchen
("Zerlegen abbrechen", "Aehnlichkeitsberechnung abbrechen", "Baumberechnung
abbrechen"). Ein Architekturtest prueft ab jetzt: **jede `cancel_*`-Methode
eines Dienstes muss im Hauptfenster gerufen werden.**

#### Ein Klick, der eine Zusammenfuehrung wegwarf

`CompareTab.has_unsaved_changes()` gibt es, seit sich Difference Blocks
uebernehmen lassen. Der `TabManager` hat nie gefragt. Wer zwei Prompts
zusammengefuehrt und dann das Schliessen-Kreuz getroffen hat, stand ohne
Rueckfrage wieder vor dem alten Stand.

Gefragt wird ueber die **Form**, nicht ueber einen Typ - der Tabverwalter kennt
den Vergleichstab nicht und soll ihn nicht kennen. Die automatische
Verdraengung bei zwoelf Tabs fragt dagegen **nicht**: sie ueberspringt solche
Tabs. Ein Dialog, der ohne Zutun des Benutzers aufgeht, waere schlimmer als ein
dreizehnter Tab.

#### Reste

* **Zehn ungenutzte Importe** entfernt (`field`, `Iterable`, `Severity`,
  `sqlite3`, `SyncReason`, `read_override`, `EvolutionTree` ...).
* **"Ordner analysieren"** im Kontextmenue des Ordnerbaums entfernt. Der Eintrag
  stand dort seit M1 dauerhaft ausgegraut mit dem Vermerk "ab M4". M4 kam - und
  die Analyse laeuft ueber die ganze Bibliothek statt ueber einen Ordner, weil
  eine Promptfamilie quer durch die Ordner geht. Ein Menuepunkt, der nie
  anklickbar wird, ist ein Versprechen, das niemand einloest.
* **Bildschirmfotos in der Wurzel** (`x.jpg`, `x2.jpg`, `x3.jpg`, `vscode.jpg`,
  `vscode.png`) in die `.gitignore` aufgenommen - sie zeigen echte Ordnernamen
  der Bibliothek und haben in einem Repository nichts verloren. Loeschen ist
  eine eigene Entscheidung.
* **`doc/MeldungenVorAbbruch.txt`** ist ein Sitzungsmitschnitt von 1.065 Zeilen,
  keine Dokumentation. Steht weiter da, gehoert aber nicht nach `doc/`.
* **`requirements-dev.txt`**: der Vermerk zu `pytest-qt` war falsch. Kein
  einziger Test benutzt `qtbot`; das Paket steht dort allein, damit es sich
  seine Qt-Bindung nicht selbst aussucht.

#### ARCHITECTURE.md wieder auf Stand

Das Dokument stand auf **M6 und 394 Tests**. Berichtigt wurden ausserdem drei
Aussagen, die dem eigenen Text widersprachen:

* "Rueckschreiben auf das NAS ist bewusst **nicht** umgesetzt" - Abschnitt 11.2
  desselben Dokuments beschreibt es seit M9 in allen Einzelheiten.
* Die Vergleichsansicht wurde noch mit `SequenceMatcher` auf Segment-Id-Listen
  und Rueckbildung ueber `char_start`/`char_end` beschrieben. Genau diese
  Rueckbildung ist in M9 abgeschafft worden, weil sie die Markierungen
  wiederholt verschoben hat.
* "Oberflaechentexte deutsch, von Beginn an durch `tr()`" - es gibt kein
  einziges `tr()` im Projekt, i18n ist in M8 gestrichen worden.

Nachgezaehlt statt abgeschrieben: **23 Tabellen, 2 Views, 40 Indizes, 3 Trigger**
(stand: 22/2/37/3), Schema **v2** (stand: v1), **69 Einstellungen** (stand:
"~50"). Neu im Abweichungsregister: Nr. 38 (Einstellungen werden ohne Warten
gespeichert) und Nr. 39 (Tabs mit ungesicherter Arbeit fragen nach).

#### Neue Tests (16)

| Bereich | Was geprueft wird |
|---|---|
| `tests/test_config.py` | Leiche wird entfernt; beim Herabstufen bleibt alles stehen; ohne Leiche wird nichts geschrieben |
| `tests/test_application.py` | Stapelgroesse kommt beim Schreiber an; Protokoll wird begrenzt; der Aufruf steht in `startup()` |
| `tests/test_tabs.py` | Nein laesst den Tab offen; Ja schliesst ihn; ein sauberer Tab wird nicht gefragt; `close_all` laesst die schmutzigen stehen; die Obergrenze fragt nicht und wirft nichts weg; ein defektes Widget blockiert nicht |
| `tests/test_architecture.py` | Jede `cancel_*`-Methode haengt am Menue; keine neue Einstellung ohne Wirkung; die Schuldliste ist nicht veraltet |

---

## Kalibrierung an der echten Bibliothek (04.09.2026)

Gemessen an **6.847 Prompts / 27.851 Bildern**, nicht am synthetischen Korpus.

### Was sich als falsch herausgestellt hat

Die bisherige Annahme in dieser Datei war, die Schwellwerte stünden auf 0,35
und 0,45 und müssten „schrittweise auf 0,5 → 0,55 → 0,6 erhöht“ werden. Beides
traf nicht zu:

* **Gespeichert waren längst 0,55 und 0,7**, nicht die Vorgabewerte. Deshalb war
  auch die befürchtete Riesenfamilie über den halben Bestand nie zu sehen.
* **Die vorgeschlagene Erhöhung auf 0,6 hätte nichts bewirkt** — sie liegt
  unterhalb des bereits eingestellten Werts.

### Der tatsächliche Befund: Verkettung

Familien entstehen über Union-Find, also **einfache Verkettung**: A und C landen
in derselben Familie, sobald ein B existiert, das beiden ähnelt — auch wenn A
und C einander unähnlich sind. Bei dieser Bibliothek trägt die Kette besonders
weit, weil **41 % aller Prompts mit demselben 500 bis 1.600 Zeichen langen
Textbaustein beginnen** (`REFERENCE IMAGE GUIDANCE (STRUCTURAL LOCK)`).

Messbar wird das an der paarweisen Ähnlichkeit zufälliger Mitgliedspaare
*innerhalb* einer Familie — nicht an `similarity_to_base`, das nur den Abstand
zur Familiensignatur misst und deshalb selbst bei starker Verkettung gut
aussieht. Bei 0,70 lagen **89 % der Paare der größten Familie unter dem
Schwellwert**, der die Familie definiert.

### Die Messreihe

Zwei Messungen waren nötig, und die erste war irreführend. Eine Simulation auf
den gespeicherten Kanten aus `prompt_neighbors` überschätzt die Abdeckung
deutlich, weil sie je Prompt rechnet. Das echte Verfahren **dedupliziert vorher
nach Text** (6.847 Prompts -> 2.618 eindeutige Texte), clustert nur die
Repräsentanten und fächert danach wieder auf. Maßgeblich sind deshalb allein
die Werte aus dem echten Lauf:

| Schwellwert | Familien | Prompts in Familien | größte Familie | Median-Kohäsion | Paare < 0,7 | Familien > 500 |
|---|---|---|---|---|---|---|
| 0,70 (vorher) | 281 | 6.327 (92 %) | 1.032 (16,3 %) | 0,693 | 49,1 % | 4 |
| 0,85 | 356 | 5.667 (83 %) | 666 (11,8 %) | 0,760 | 27,1 % | 2 |
| **0,90 (gesetzt)** | **353** | **4.711 (69 %)** | **283 (6,0 %)** | **0,899** | **0,1 %** | **0** |

Jeder Lauf dauert rund 85 Sekunden.

**0,90 ist gesetzt**, weil dort drei Dinge zusammenkommen:

1. Die größte Familie hat Medianähnlichkeit 0,899, und nur **0,1 %** der
   Mitgliedspaare liegen unter 0,7 - vorher waren es 49 %. Die Familien halten
   wirklich zusammen, statt über Ketten zusammengehalten zu werden.
2. Es sind weiterhin **echte Varianten**, keine Dublettenhaufen: nur 12 % der
   Paare sind identische Texte. 3.456 Varianten sind eingetragen.
3. **Keine Familie sprengt mehr die 500-Knoten-Grenze** der Evolution
   (`analysis.evolution_max_nodes`); bei 0,85 waren es noch zwei, vorher vier.
   Damit ist die willkürliche Kürzung der Elternsuche aus M6 gegenstandslos -
   die drei größten Bäume (283, 220, 133 Knoten) sind vollständig.

**Der Preis, bewusst in Kauf genommen:** 1.616 Prompts verlieren ihre
Familienzugehörigkeit (6.327 -> 4.711, von 92 auf 69 Prozent des Bestands).
Das sind genau die, die vorher nur über Ketten drinhingen. Für sie entfallen
Familie, Varianten und Baum - **nicht** aber die ähnlichen Bilder: die
Nachbarschaften in `prompt_neighbors` hängen an
`analysis.similarity_threshold` (0,55) und sind unverändert, alle 532.606
Kanten stehen weiter zur Verfügung.

Wer die Abdeckung höher gewichtet als die Geschlossenheit der Familien, nimmt
0,85: 83 Prozent Abdeckung, aber jedes vierte Mitgliedspaar unähnlich und zwei
Bäume wieder über der Knotengrenze.

### Geprüft und verworfen

**Gegenseitige Kanten** (nur vereinigen, wenn beide Prompts einander in ihrer
Top-k-Liste führen) — der naheliegende Gegenentwurf zur Verkettung. Gemessen:
die größte Familie sank von 1.032 nur auf 1.016. Bei k=20 und vielen
Fast-Dubletten sind ohnehin fast alle Kanten gegenseitig. **Nicht gebaut.**

### Ausreißer im Bestand (kein Defekt)

`GeminiPrompts.txt` (92.160 Wörter, 14.340 Segmente) und `NanoBananaPromts.txt`
(31.916 Wörter) sind **Sammeldateien** — Notizsammlungen vieler Prompts, kein
Bild-Prompt-Paar. Insgesamt liegen nur 6 Prompts über 2.000 Wörtern, eine Datei
ist leer. Sie verzerren die Statistik kaum, erklären aber die Spannweite.

### Angewandt am 04.09.2026

Der Wert ist in der **bestehenden Bibliothek gesetzt** und die Analyse ist
durchgelaufen (85 s). Vorher wurde eine Sicherung angelegt:
`%LOCALAPPDATA%\PicPromptEditor\library.db.vor-kalibrierung` (194 MB). Sie kann
gelöscht werden, sobald die neue Einteilung im Betrieb überzeugt.

Die Vorgabe in `app/config/schema.py` gilt zusätzlich für neue Installationen.
`analysis.similarity_threshold` (0,55) blieb unverändert.

### Zwei Beobachtungen für M8

**1. Eine Schwellwertänderung löst keine Neuberechnung aus.** Die Analyse
läuft nur, wenn sie ohnehin angestoßen wird; das Ändern von
`analysis.cluster_threshold` allein bewirkt nichts. Beim Einstellungsdialog in
M8 muss eine Änderung an den Analyseparametern also **selbst eine Neuanalyse
anstoßen** - sonst stellt der Benutzer einen Wert ein, sieht keine Wirkung und
hält die Einstellung für kaputt.

**2. Ein Analyselauf ist einmalig mit einem unsinnigen Fehler abgebrochen:**
`TypeError: 'function' object does not support the context manager protocol`,
gemeldet an `app/analysis/similarity.py` in `_weight()` - einer Funktion, die
gar kein `with` enthält. Der Job meldete `finished` ohne `result`, die Analyse
blieb still wirkungslos. **In zwei Wiederholungen nicht reproduzierbar.** Steht
als `id=2` in `error_log`. Verdacht: eine Wechselwirkung beim Neuübersetzen der
.pyc-Dateien, während ein Vorgängerprozess noch lief - unbewiesen. Falls es
wiederkehrt, ist der Fehlereintrag der Anfang.

---

## Offene Punkte, die eine Entscheidung brauchen

**1. Schwellwerte kalibriert — erledigt am 04.09.2026.**
`analysis.cluster_threshold` steht jetzt auf **0,90** (Vorgabe in
`app/config/schema.py`, dort mit der Messreihe begründet). Einzelheiten unten
unter „Kalibrierung“.

**2. Zeitstempel als Evolutionsachse — geprüft, unbedenklich.**
6.833 von 6.847 Prompts haben verschiedene `modified_time` (99,8 %). Die
Bibliothek wurde also nicht umkopiert, die Zeitachse trägt, der Baum wird nicht
flach. Punkt geschlossen.

**3. Weitere Promptformate.** Die Formaterkennung ist an einer Beispieldatei
geeicht (Langform-Prosa mit CAPS-Sektionen). Der A1111-Parser existiert bereits.

**4. Prompt-Rückschreiben — umgesetzt am 05.09.2026 (M9).** Alle drei
geforderten Teile stehen: atomares Schreiben (`app/filesystem/prompt_writer.py`),
Konflikterkennung gegen Größe und `mtime`, und die Reparse-Kette über
`parse_version = 0`. **Der Editor bleibt schreibgeschützt** — geändert wird
ausschließlich über die Übernahme eines Difference Blocks in der
Vergleichsansicht, mit Rückfrage vor jedem Schreibvorgang. Abschaltbar über
`compare.allow_write_back`.

Was dabei offen bleibt: der Watcher bemerkt die eigene Änderung und stößt einen
Abgleich an. Das ist unschädlich (die Signatur in der Datenbank ist bereits
aktuell, die Datei gilt als unverändert), aber es ist ein Lauf, den man sich
sparen könnte.

**5. Zweiter Blick auf `compare.similarity_threshold`.** Steht auf 0,5, gewählt
und nicht gemessen. Er entscheidet, ab wann zwei Zeilen innerhalb eines
Änderungsblocks einander zugeordnet und wortweise verfeinert werden. Eine
Messreihe an der echten Bibliothek — wie sie M8 für `cluster_threshold` gebracht
hat — steht noch aus.

---

## Was beim Bauen zu beachten ist

**Die Architekturtests sind bindend.** `tests/test_architecture.py` prüft per
AST: keine Importe entgegen der Datenflussrichtung, kein SQL außerhalb der
Repository-Schicht, kein Datei- oder Prozesszugriff außerhalb von
`app/filesystem`, ein Schichtvertrag im `__init__.py` jedes Pakets. Sie haben
beim Bau von M1 drei bereits eingebaute Verstöße gefunden.

**Regeln, die sich über sechs Meilensteine bewährt haben:**

- Ein Signalhandler darf nichts tun, was warten kann. Aufräumarbeit gehört ans
  Ende des Jobs, nicht in den Handler.
- Jedes Signal mit mehreren Empfängern trägt eine Anfragenummer; der Empfänger
  verwirft, was nicht seine ist.
- `future.result()` des `DbWriter` kehrt erst nach dem COMMIT zurück.
- Rechnungen, die Qt-Widgets betreffen, als **reine Funktion** auslagern. Qt
  verwaltet Rollbalken-Reichweiten selbst und überschreibt gesetzte Werte.
- Neue Parameter in bestehende Signaturen nur **keyword-only** einfügen. Ein
  positionell eingefügtes `palette` vor `parent` hat in M3 die Hervorhebung in
  allen Tabs stillschweigend zerstört.
- Was die Oberfläche anzeigt, muss **unterscheidbar** sein. Die Knoten des
  Evolutionsbaums trugen zunächst alle denselben Promptanfang — technisch
  richtig, praktisch wertlos.

**Umgebungsfalle:** PyQt6 6.11 und PySide6 6.9 liegen nebeneinander. Wird PySide6
zuerst geladen, scheitert PyQt6 mit `DLL load failed while importing QtCore`.
`tests/conftest.py` legt die Bindung fest. Bei neuen Abhängigkeiten prüfen, ob
sie PySide6 oder `qtpy` nachziehen.

**Werkzeuge:**

```
python tools/gen_testlib.py ZIEL --pairs 2000 --folders 8 --orphans 4
python tools/bench_analysis.py --prompts 20000 --families 500
```
