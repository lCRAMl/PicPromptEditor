"""Typisierte Registry aller Einstellungen.

Spezifikation 11 fordert je Eintrag Schluessel, Wert, Datentyp, Standardwert und
Validierungsregeln - und ein System ohne hardcodierte Parameter. Diese Datei ist
die eine Stelle, an der ein Parameter definiert wird; ueberall sonst wird er nur
noch ueber SettingsService.get() gelesen.

requires_restart markiert Einstellungen, die laufende Komponenten nicht im Betrieb
uebernehmen koennen. Alles andere loest ein Aenderungsereignis aus, damit
betroffene Komponenten neu konfigurieren.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

CONFIG_VERSION = 1


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SettingDef:
    key: str
    type: str  # bool | int | float | str | path | list
    default: Any
    group: str
    description: str
    validator: Callable[[Any], Any] | None = None
    requires_restart: bool = False

    def coerce(self, raw: Any) -> Any:
        """Wandelt einen Rohwert in den deklarierten Typ. Wirft ValidationError."""
        try:
            if raw is None:
                raise ValidationError("Wert fehlt")
            if self.type == "bool":
                if isinstance(raw, bool):
                    value: Any = raw
                else:
                    text = str(raw).strip().lower()
                    if text in ("1", "true", "yes", "on"):
                        value = True
                    elif text in ("0", "false", "no", "off"):
                        value = False
                    else:
                        raise ValidationError("kein Wahrheitswert: " + str(raw))
            elif self.type == "int":
                value = int(str(raw).strip())
            elif self.type == "float":
                value = float(str(raw).strip())
            elif self.type == "list":
                value = raw if isinstance(raw, list) else json.loads(str(raw))
                if not isinstance(value, list):
                    raise ValidationError("keine Liste")
            else:  # str, path
                value = str(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError(str(exc)) from exc
        return self.validator(value) if self.validator else value

    @property
    def bounds(self) -> tuple[float, float] | None:
        """Zulaessiger Bereich, falls der Validator einen kennt."""
        return getattr(self.validator, "bounds", None)

    @property
    def allowed(self) -> tuple[str, ...] | None:
        """Zulaessige Werte, falls der Validator eine Auswahl vorgibt."""
        return getattr(self.validator, "allowed", None)

    def serialize(self, value: Any) -> str:
        if self.type == "bool":
            return "1" if value else "0"
        if self.type == "list":
            return json.dumps(value, ensure_ascii=False)
        return str(value)


# Validatoren ------------------------------------------------------------------


# Die Validatoren tragen ihre Regel als Attribut mit sich: `bounds` bei
# Bereichen, `allowed` bei Auswahlen. Ohne das muesste der Einstellungsdialog
# dieselben Grenzen ein zweites Mal fuehren - und diese Datei ist ausdruecklich
# die EINE Stelle, an der ein Parameter definiert wird. Die Attribute sind
# reine Auskunft; geprueft wird weiterhin allein in check().


def int_range(low: int, high: int) -> Callable[[Any], int]:
    def check(value: Any) -> int:
        number = int(value)
        if not low <= number <= high:
            raise ValidationError("muss zwischen " + str(low) + " und " + str(high) + " liegen")
        return number

    check.bounds = (low, high)  # type: ignore[attr-defined]
    return check


def float_range(low: float, high: float) -> Callable[[Any], float]:
    def check(value: Any) -> float:
        number = float(value)
        if not low <= number <= high:
            raise ValidationError("muss zwischen " + str(low) + " und " + str(high) + " liegen")
        return number

    check.bounds = (low, high)  # type: ignore[attr-defined]
    return check


def one_of(*allowed: str) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        text = str(value)
        if text not in allowed:
            raise ValidationError("muss einer von " + ", ".join(allowed) + " sein")
        return text

    check.allowed = tuple(allowed)  # type: ignore[attr-defined]
    return check


def scheme_colors(value: Any) -> str:
    """Farbe je Farbschema, als JSON-Objekt - oder leer fuer die Vorgabe.

    Warum nicht einfach eine Farbe? Weil die Anwendung ein helles und ein
    dunkles Schema hat. Ein einziger Wert wuerde in einem der beiden landen,
    wo er nicht lesbar ist - ein helles Blau auf dunklem Grund verschwindet.
    Gespeichert wird deshalb je Schema, angezeigt wird im Einstellungsdialog
    trotzdem nur ein Regler: der fuer das gerade aktive Schema.

    Zulaessig: "" (Vorgabe verwenden) oder {"light": "#rrggbb", "dark": "..."}.
    """
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ValidationError("kein gueltiges JSON: " + str(exc)) from exc
    if not isinstance(parsed, dict):
        raise ValidationError("Objekt mit den Schluesseln light und dark erwartet")

    clean: dict[str, str] = {}
    for scheme, color in parsed.items():
        if scheme not in ("light", "dark"):
            raise ValidationError("unbekanntes Schema: " + str(scheme))
        code = str(color).strip()
        if not _is_hex_color(code):
            raise ValidationError("keine Farbe im Format #rrggbb: " + code)
        clean[scheme] = code.lower()
    return json.dumps(clean, sort_keys=True) if clean else ""


def _is_hex_color(text: str) -> bool:
    if len(text) != 7 or not text.startswith("#"):
        return False
    return all(c in "0123456789abcdefABCDEF" for c in text[1:])


def str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError("keine Liste")
    return [str(v) for v in value]


def int_list_sorted(value: Any) -> list[int]:
    if not isinstance(value, list) or not value:
        raise ValidationError("nicht leere Liste erwartet")
    numbers = sorted({int(v) for v in value})
    if any(n < 16 or n > 2048 for n in numbers):
        raise ValidationError("Groessenstufen muessen zwischen 16 und 2048 liegen")
    return numbers


# Registry ---------------------------------------------------------------------

_DEFS: tuple[SettingDef, ...] = (
    # --- Bibliothek und NAS ---------------------------------------------------
    SettingDef("library.roots", "list", [], "library",
               "Ueberwachte Wurzelverzeichnisse (NAS-Pfade)", str_list),
    SettingDef("library.image_extensions", "list", [".jpg", ".jpeg", ".png"], "library",
               "Als Bild behandelte Dateiendungen", str_list),
    SettingDef("library.prompt_extension", "str", ".txt", "library",
               "Endung der Promptdateien"),
    SettingDef("library.follow_subfolders", "bool", True, "library",
               "Unterverzeichnisse mit einbeziehen"),

    # --- Indexierung ----------------------------------------------------------
    SettingDef("index.scan_on_startup", "bool", True, "index",
               "Inkrementellen Scan nach Programmstart ausfuehren"),
    SettingDef("index.background_scan_interval_s", "int", 900, "index",
               "Periodischer Hintergrundabgleich in Sekunden (0 = aus)",
               int_range(0, 86400)),
    SettingDef("index.watcher_enabled", "bool", True, "index",
               "Datei-Watcher als Beschleuniger nutzen"),
    SettingDef("index.watcher_debounce_ms", "int", 1500, "index",
               "Sammelfenster fuer Watcher-Ereignisse", int_range(0, 60000)),
    SettingDef("index.hash_images", "bool", False, "index",
               "Bilder vollstaendig hashen (langsam auf NAS, nur bei Bedarf)"),
    SettingDef("index.hash_partial_bytes", "int", 65536, "index",
               "Bytes am Datei-Anfang und -Ende fuer den Teilhash",
               int_range(4096, 4194304)),
    SettingDef("index.mtime_tolerance_s", "float", 2.0, "index",
               "Toleranz beim Zeitstempelvergleich (SMB rundet teils auf 2 s)",
               float_range(0.0, 60.0)),
    SettingDef("index.auto_reindex_on_schema_change", "bool", True, "index",
               "Nach Schemaaenderung automatisch neu indizieren"),

    # --- Nebenlaeufigkeit -----------------------------------------------------
    SettingDef("jobs.interactive_threads", "int", 3, "jobs",
               "Worker fuer Suche, Bild- und Promptladen", int_range(1, 16), True),
    SettingDef("jobs.io_threads", "int", 4, "jobs",
               "Worker fuer NAS- und Thumbnail-Zugriffe; mehr ist auf NAS langsamer",
               int_range(1, 32), True),
    SettingDef("jobs.cpu_threads", "int", 0, "jobs",
               "Worker fuer Analyse (0 = Kerne minus zwei)", int_range(0, 64), True),
    SettingDef("jobs.db_write_batch", "int", 256, "jobs",
               "Schreibaufgaben pro Transaktion", int_range(1, 10000)),

    # --- Thumbnails und Cache -------------------------------------------------
    SettingDef("thumbnails.sizes", "list", [128, 256, 512], "cache",
               "Erzeugte Groessenstufen in Pixel", int_list_sorted),
    SettingDef("thumbnails.default_size", "int", 256, "cache",
               "Groesse im Thumbnail Explorer", int_range(16, 2048)),
    SettingDef("thumbnails.format", "str", "webp", "cache",
               "Internes Cacheformat", one_of("webp", "jpeg", "png")),
    SettingDef("thumbnails.quality", "int", 82, "cache",
               "Kompressionsqualitaet", int_range(1, 100)),
    SettingDef("thumbnails.max_cache_mb", "int", 4096, "cache",
               "Obergrenze des persistenten Cache in MB", int_range(64, 1048576)),
    SettingDef("thumbnails.preload_rows", "int", 3, "cache",
               "Zusaetzlich vorgeladene Zeilen ober- und unterhalb des Sichtbereichs",
               int_range(0, 50)),
    SettingDef("cache.ram_thumbnails_mb", "int", 256, "cache",
               "RAM-Cache fuer Vorschaubilder in MB", int_range(16, 16384)),
    SettingDef("cache.ram_prompts", "int", 2000, "cache",
               "Anzahl im RAM gehaltener Prompts", int_range(0, 200000)),
    SettingDef("cache.cleanup_interval_s", "int", 3600, "cache",
               "Intervall der Cachebereinigung", int_range(0, 604800)),

    # --- Suche ----------------------------------------------------------------
    SettingDef("search.fts_prefix_matching", "bool", True, "search",
               "Suchbegriffe als Praefix behandeln"),
    SettingDef("search.max_results", "int", 5000, "search",
               "Obergrenze der Treffer pro Suche", int_range(10, 1000000)),
    SettingDef("search.debounce_ms", "int", 250, "search",
               "Verzoegerung der Suche beim Tippen", int_range(0, 5000)),

    # --- Analyse --------------------------------------------------------------
    SettingDef("analysis.enabled", "bool", True, "analysis",
               "Hintergrundanalyse aktiv"),
    # Die beiden Schwellwerte sind an tools/bench_analysis.py kalibriert, nicht
    # geraten. Bei einem Korpus mit bekannter Wahrheit (500 Familien zu je 40
    # Prompts) findet die Kette bei 0.45 genau diese 500 Familien wieder;
    # bei 0.70 findet sie keine einzige. Der Speicherwert liegt bewusst
    # darunter, damit der Cluster-Schwellwert ohne Neuberechnung der
    # Aehnlichkeiten gesenkt werden kann.
    SettingDef("analysis.similarity_threshold", "float", 0.35, "analysis",
               "Ab dieser Aehnlichkeit wird eine Nachbarschaft gespeichert",
               float_range(0.0, 1.0)),
    # An einer echten Bibliothek kalibriert (6847 Prompts, 27851 Bilder), nicht
    # am synthetischen Korpus. Der Grund fuer den hohen Wert ist die Verkettung:
    # Familien entstehen ueber Union-Find, also einfache Verkettung. Zwei Prompts
    # A und C landen in derselben Familie, sobald es ein B gibt, das beiden
    # aehnelt - auch wenn A und C einander unaehnlich sind. Bei Prompts, die sich
    # einen langen Textbaustein teilen (hier: 41 Prozent beginnen mit demselben
    # 500 bis 1600 Zeichen langen Vorspann), traegt diese Kette weit.
    #
    # Gemessen an der groessten Familie, Median der paarweisen Aehnlichkeit
    # zufaelliger Mitgliedspaare:
    #
    #   0.70 -> 1032 Mitglieder, Median 0.46, 89 % der Paare unter dem Schwellwert
    #   0.80 ->  697 Mitglieder, Median 0.68
    #   0.90 ->  283 Mitglieder, Median 0.90,  0.5 % der Paare darunter
    #   0.95 ->  104 Mitglieder, aber nur noch Dublettenhaufen
    #
    # 0.90 ist der Punkt, an dem die Familien halten, ohne zu Dubletten zu
    # zerfallen (nur 12 Prozent der Paare sind identische Texte), und zugleich
    # der Punkt, an dem keine Familie mehr die 500-Knoten-Grenze der Evolution
    # sprengt. Eine hoehere Gegenseitigkeitsregel wurde geprueft und verworfen:
    # sie senkte die groesste Familie nur von 1032 auf 1016.
    SettingDef("analysis.cluster_threshold", "float", 0.90, "analysis",
               "Ab dieser Aehnlichkeit gehoeren zwei Prompts zur selben Familie",
               float_range(0.0, 1.0)),
    SettingDef("analysis.neighbors_k", "int", 20, "analysis",
               "Gespeicherte naechste Nachbarn pro Prompt", int_range(1, 200)),
    SettingDef("analysis.base_prompt_min_commonness", "float", 0.6, "analysis",
               "Ab welchem Anteil ein Segment als Basisbestandteil gilt",
               float_range(0.0, 1.0)),
    SettingDef("analysis.change_threshold", "float", 0.4, "analysis",
               "Ab dieser Wortueberlappung (Dice) gelten zwei Segmente als"
               " geaendert statt als entfernt und neu", float_range(0.0, 1.0)),
    SettingDef("analysis.min_segment_words", "int", 1, "analysis",
               "Kuerzere Segmente werden verworfen", int_range(1, 20)),
    SettingDef("analysis.evolution_auto_rebuild", "bool", True, "analysis",
               "Evolutionsbaeume im Hintergrund nachziehen"),
    SettingDef("analysis.evolution_max_nodes", "int", 500, "analysis",
               "Knotenobergrenze eines Evolutionsbaums", int_range(10, 20000)),

    # --- Parsing --------------------------------------------------------------
    SettingDef("parsing.format", "str", "auto", "parsing",
               "Promptformat", one_of("auto", "prose_sectioned", "a1111", "tag_list", "plain")),
    SettingDef("parsing.negative_markers", "list",
               ["NEGATIVE PROMPT", "Negative prompt", "NEGATIVE"], "parsing",
               "Marker, ab dem Segmente als negativ gelten", str_list),
    SettingDef("parsing.index_words", "bool", False, "parsing",
               "Zusaetzlich einzelne Woerter als Segmente indizieren (FTS5 deckt das ab)"),

    # --- Oberflaeche ----------------------------------------------------------
    SettingDef("ui.theme", "str", "system", "ui",
               "Farbschema", one_of("system", "light", "dark")),
    SettingDef("ui.show_folder_counts", "bool", True, "ui",
               "Bild- und Promptanzahl im Ordnerbaum anzeigen"),
    SettingDef("ui.editor_font_size", "int", 10, "ui",
               "Schriftgroesse im Prompt-Editor", int_range(6, 48)),
    SettingDef("ui.editor_read_only", "bool", True, "ui",
               "Prompt-Editor schreibgeschuetzt (das Ausleiten aus der"
               " Vergleichsansicht steuert compare.allow_export)"),
    # Vergleichsansicht ------------------------------------------------------
    # Das Ausleiten schreibt IMMER in eine neue, vom Benutzer gewaehlte Datei.
    # Eine bestehende Promptdatei wird nie ueberschrieben - das ist nicht nur
    # vereinbart, sondern baulich durchgesetzt: der PromptService kennt keine
    # Methode, die in prompt.full_path schreibt.
    SettingDef("compare.allow_export", "bool", True, "compare",
               "Zusammengefuehrten Prompt aus der Vergleichsansicht in eine"
               " neue Datei speichern duerfen"),
    SettingDef("compare.word_wrap", "bool", True, "compare",
               "Zeilen in der Vergleichsansicht umbrechen (aus entspricht"
               " WinMerge, verlangt dafuer waagerechtes Scrollen)"),
    SettingDef("compare.intra_line_mode", "str", "word", "compare",
               "Feinvergleich innerhalb geaenderter Zeilen",
               one_of("word", "character")),
    SettingDef("compare.ignore_case", "bool", False, "compare",
               "Gross- und Kleinschreibung beim Vergleich ignorieren"),
    SettingDef("compare.ignore_whitespace_changes", "bool", False, "compare",
               "Aenderungen am Leerraum beim Vergleich ignorieren"),
    SettingDef("compare.ignore_blank_lines", "bool", False, "compare",
               "Leerzeilen beim Vergleich ignorieren"),
    SettingDef("compare.detect_moved", "bool", False, "compare",
               "Verschobene Textbloecke als solche kennzeichnen"),
    SettingDef("compare.similarity_threshold", "float", 0.5, "compare",
               "Ab dieser Aehnlichkeit werden zwei Zeilen innerhalb eines"
               " Aenderungsblocks einander zugeordnet", float_range(0.0, 1.0)),
    SettingDef("compare.algorithm", "str", "sequence_matcher", "compare",
               "Verfahren fuer den Zeilenabgleich",
               one_of("sequence_matcher", "patience")),
    SettingDef("ui.highlight_min_idf", "float", 1.5, "ui",
               "Ab welcher Unterscheidungskraft ein Segment hervorgehoben wird",
               float_range(0.0, 20.0)),
    SettingDef("ui.restore_session", "bool", True, "ui",
               "Offene Tabs beim Start wiederherstellen"),
    SettingDef("ui.snap_window_to_tiles", "bool", True, "ui",
               "Fensterbreite auf ganze Kachelspalten einrasten"),
    # Die Einfaerbung im Prompt-Editor. Je Farbschema gespeichert, im Dialog
    # als ein Regler sichtbar - siehe scheme_colors().
    SettingDef("ui.color_segment_rare", "str", "", "ui",
               "Farbe seltener Segmente im Editor", scheme_colors),
    SettingDef("ui.color_segment_common", "str", "", "ui",
               "Farbe haeufiger Segmente (Textbausteine) im Editor", scheme_colors),
    SettingDef("ui.color_segment_negative", "str", "", "ui",
               "Farbe von Negativ-Prompt-Segmenten im Editor", scheme_colors),

    # Die Rahmenfarbe der Oberflaeche. Alle Flaechen tragen dieselbe Farbe;
    # die Rahmen sind damit das Einzige, was noch gliedert - deshalb sind
    # gerade sie einstellbar. Ebenfalls je Farbschema gespeichert: ein Blau,
    # das auf Schwarz stimmt, verschwindet auf Weiss.
    SettingDef("ui.color_accent", "str", "", "ui",
               "Akzentfarbe: Rahmen des aktiven Elements, Linie ueber dem"
               " aktiven Tab, Auswahl", scheme_colors),
    SettingDef("ui.color_accent_dim", "str", "", "ui",
               "Linienfarbe: Rahmen aller uebrigen Bedienelemente",
               scheme_colors),
    # Die Flaechenfarbe des ganzen Programms - Leisten, Editor, Kachelraster
    # und Eingabefelder tragen dieselbe. Schriftfarbe und Platzhalter werden
    # daraus abgeleitet, damit eine freie Wahl das Programm nicht unlesbar
    # macht; siehe _derive_from_background in app/gui/theme.py.
    SettingDef("ui.color_background", "str", "", "ui",
               "Hintergrundfarbe des Programms (Schrift passt sich an)",
               scheme_colors),

    # --- Betrieb --------------------------------------------------------------
    SettingDef("system.log_level", "str", "INFO", "system",
               "Logging-Stufe", one_of("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")),
    SettingDef("system.debug_mode", "bool", False, "system",
               "Zusatzdiagnose aktivieren"),
    SettingDef("system.error_log_max_rows", "int", 5000, "system",
               "Obergrenze der Zeilen im error_log", int_range(100, 1000000)),
    SettingDef("system.backup_on_migration", "bool", True, "system",
               "Datenbank vor jeder Schemamigration sichern"),
    SettingDef("system.nas_probe_interval_s", "int", 30, "system",
               "Intervall der NAS-Erreichbarkeitspruefung", int_range(5, 3600)),
    SettingDef("system.nas_probe_timeout_s", "float", 3.0, "system",
               "Zeitlimit einer NAS-Pruefung", float_range(0.5, 60.0)),
    SettingDef("system.config_version", "int", CONFIG_VERSION, "system",
               "Version des Einstellungsschemas", int_range(0, 100000)),
)

SETTINGS: dict[str, SettingDef] = {d.key: d for d in _DEFS}


def all_defs() -> Iterable[SettingDef]:
    return _DEFS


def groups() -> list[str]:
    seen: list[str] = []
    for d in _DEFS:
        if d.group not in seen:
            seen.append(d.group)
    return seen


def defaults() -> dict[str, Any]:
    return {d.key: d.default for d in _DEFS}
