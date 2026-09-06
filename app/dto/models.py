"""Grenzobjekte zwischen Service- und GUI-Schicht.

Alle eingefroren und ohne Verhalten. Die Oberflaeche bekommt niemals ein
sqlite3.Row, ein Repository-Objekt oder einen Pfad, den sie selbst anfassen
koennte.

ImageItem ist bewusst schmal gehalten. Es ist das Element, von dem das
Thumbnail-Grid zehntausende im Speicher haelt; jedes zusaetzliche Feld
multipliziert sich mit der Bibliotheksgroesse. Der Prompttext gehoert deshalb
nicht hinein, sondern wird ueber PromptData einzeln nachgeladen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class PairState(IntEnum):
    COMPLETE = 0
    IMAGE_ONLY = 1
    PROMPT_ONLY = 2


class ThumbnailState(IntEnum):
    UNKNOWN = 0
    READY = 1
    FAILED = 2


@dataclass(frozen=True, slots=True)
class FolderNode:
    id: int
    path: str
    name: str
    parent_id: int | None
    depth: int
    image_count: int
    prompt_count: int
    last_scan: float | None
    is_present: bool = True
    #: Bilder im Unterordner "ai" dieses Ordners. In dieser Bibliothek liegen
    #: 99,6 Prozent aller Prompts dort; die Zahl des Elternordners sagt darum
    #: wenig, die des ai-Ordners alles.
    ai_image_count: int = 0
    ai_prompt_count: int = 0

    @property
    def has_content(self) -> bool:
        return self.image_count > 0


@dataclass(frozen=True, slots=True)
class ImageItem:
    """Ein Element des Thumbnail-Grids."""

    id: int
    folder_id: int
    filename: str
    stem: str
    full_path: str
    thumbnail_key: str | None
    thumbnail_state: ThumbnailState
    prompt_id: int | None
    file_size: int
    modified_time: int
    is_present: bool = True

    @property
    def has_prompt(self) -> bool:
        return self.prompt_id is not None


@dataclass(frozen=True, slots=True)
class PromptData:
    id: int
    filename: str
    full_path: str
    text: str
    encoding: str
    token_count: int
    word_count: int
    is_present: bool = True
    #: Groesse und Aenderungszeit der Datei, wie sie beim letzten Abgleich
    #: gesehen wurden. Grundlage der Konflikterkennung beim Zurueckschreiben:
    #: weicht die Datei davon ab, hat jemand anders sie inzwischen angefasst.
    file_size: int = 0
    modified_time: int = 0


@dataclass(frozen=True, slots=True)
class ScanStats:
    """Ergebnis eines Scanlaufs."""

    folders_seen: int = 0
    files_seen: int = 0
    new_images: int = 0
    new_prompts: int = 0
    changed: int = 0
    removed: int = 0
    #: Prompts, die zu mehr als einem Bild gehoeren. Kein Fehler: eine
    #: Bildserie aus einem Prompt ist der uebliche Fall. Nur eine Kennzahl.
    pair_conflicts: int = 0
    errors: int = 0
    duration: float = 0.0
    cancelled: bool = False

    def merged(self, other: "ScanStats") -> "ScanStats":
        return ScanStats(
            folders_seen=self.folders_seen + other.folders_seen,
            files_seen=self.files_seen + other.files_seen,
            new_images=self.new_images + other.new_images,
            new_prompts=self.new_prompts + other.new_prompts,
            changed=self.changed + other.changed,
            removed=self.removed + other.removed,
            pair_conflicts=self.pair_conflicts + other.pair_conflicts,
            errors=self.errors + other.errors,
            duration=self.duration + other.duration,
            cancelled=self.cancelled or other.cancelled,
        )

    def summary(self) -> str:
        parts = []
        if self.new_images:
            parts.append(str(self.new_images) + " neu")
        if self.changed:
            parts.append(str(self.changed) + " geaendert")
        if self.removed:
            parts.append(str(self.removed) + " entfernt")
        if self.pair_conflicts:
            parts.append(str(self.pair_conflicts) + " Prompts mehrfach verwendet")
        if self.errors:
            parts.append(str(self.errors) + " Fehler")
        return ", ".join(parts) if parts else "keine Aenderungen"


@dataclass(frozen=True, slots=True)
class LibraryStats:
    image_count: int = 0
    prompt_count: int = 0
    distinct_prompt_texts: int = 0
    folder_count: int = 0
    orphan_count: int = 0


@dataclass(frozen=True, slots=True)
class ErrorEntry:
    """Ein aggregierter Eintrag der Problemliste.

    Liegt hier und nicht im Repository, weil die Problemliste der Oberflaeche ihn
    anzeigt. Ein GUI-Modul, das ein Repository importiert, um an einen Typ zu
    kommen, haette die Schichtgrenze bereits durchbrochen.
    """

    id: int
    timestamp: float
    last_seen: float
    severity: int
    severity_label: str
    source: str
    message: str
    details: str | None
    count: int
    resolved: bool


@dataclass(frozen=True, slots=True)
class ComparePair:
    """Alles, was die Vergleichsansicht braucht - in einem Zug geladen.

    Die Ansicht soll nicht vier Einzelabfragen absetzen und dabei vier Mal auf
    eine Antwort warten; ein Job laedt beide Seiten und den fertigen Diff.
    """

    left_image: ImageItem
    right_image: ImageItem
    left_prompt: PromptData | None
    right_prompt: PromptData | None
    #: Segmentweiser Vergleich (app.analysis.diff.PromptDiff). Grundlage der
    #: Uebereinstimmungsquote in der Statuszeile - sie zaehlt Saetze, nicht
    #: Zeilen, und ist damit die aussagekraeftigere Kennzahl.
    diff: object = None
    #: Zeilenweiser Vergleich (app.analysis.textdiff.ComparisonResult). Das
    #: Modell, aus dem die Ansicht IHRE GESAMTE Darstellung ableitet -
    #: Ausrichtung, Platzhalter, Faerbung, Sprungmarken.
    comparison: object = None
    request_id: int = 0

    @property
    def complete(self) -> bool:
        return self.left_prompt is not None and self.right_prompt is not None


@dataclass(frozen=True, slots=True)
class ResultSet:
    """Inhalt des Thumbnail Explorers samt Herkunft fuer die Statuszeile."""

    items: list[ImageItem] = field(default_factory=list)
    origin: str = ""
    request_id: int = 0
    truncated: bool = False

    def __len__(self) -> int:
        return len(self.items)
