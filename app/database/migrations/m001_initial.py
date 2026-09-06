"""Schema Version 1 - vollstaendiges Grundschema von library.db.

Abweichungen von den Spezifikationsdateien sind unten jeweils am Ort begruendet.
Die wichtigsten:

* "exists" heisst hier is_present. "exists" ist in SQLite ein Schluesselwort und
  laesst sich unquotiert nicht als Spaltenname verwenden.
* token_frequency haelt nur Zaehler. Die abgeleiteten Groessen commonness und idf
  liefert die View token_stats, weil beide von der Gesamtzahl der Prompts abhaengen
  und persistiert bei jedem Import komplett neu geschrieben werden muessten.
* Zusaetzliche Tabellen: prompt_sections, prompt_params, prompt_clusters,
  cluster_members, prompt_neighbors, dirty_prompts, analysis_state, thumbnails,
  library_stats, schema_migrations.
"""

from __future__ import annotations

import sqlite3

from app.database.sqlscript import exec_script

VERSION = 1
NAME = "initial"

SCHEMA = """
-- ---------------------------------------------------------------------------
-- Ordner
-- parent_id und depth sind Zusatzfelder: der Ordnerbaum der GUI muss aus der
-- Datenbank aufgebaut werden, nicht durch Nachscannen des NAS. Ohne die
-- Elternbeziehung waere jede Baumdarstellung ein Dateisystemzugriff.
-- ---------------------------------------------------------------------------
CREATE TABLE folders (
    id            INTEGER PRIMARY KEY,
    path          TEXT    NOT NULL,
    path_norm     TEXT    NOT NULL,
    parent_id     INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    depth         INTEGER NOT NULL DEFAULT 0,
    is_root       INTEGER NOT NULL DEFAULT 0 CHECK (is_root IN (0, 1)),
    last_scan     REAL,
    last_modified REAL,
    image_count   INTEGER NOT NULL DEFAULT 0,
    prompt_count  INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    is_present    INTEGER NOT NULL DEFAULT 1 CHECK (is_present IN (0, 1)),
    created_at    REAL    NOT NULL DEFAULT (unixepoch('subsec')),
    updated_at    REAL    NOT NULL DEFAULT (unixepoch('subsec'))
);
CREATE UNIQUE INDEX ux_folders_path_norm ON folders(path_norm);
CREATE INDEX ix_folders_parent ON folders(parent_id);
CREATE INDEX ix_folders_active ON folders(active, is_present);

-- ---------------------------------------------------------------------------
-- Prompts
-- folder_id ist ein Zusatzfeld gegenueber Spezifikation 03: ohne es braucht
-- jede ordnerbezogene Promptabfrage einen Join ueber images, und ein Prompt
-- ohne Bild waere keinem Ordner zuordenbar.
-- text_hash ist die Grundlage der Analyse-Deduplizierung.
-- parse_json haelt die Struktur mit Zeichenpositionen fuer Editor und Diff;
-- prompt_tokens bleibt der relationale Index fuer Suche und Analyse.
-- ---------------------------------------------------------------------------
CREATE TABLE prompts (
    id             INTEGER PRIMARY KEY,
    folder_id      INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    filename       TEXT    NOT NULL,
    full_path      TEXT    NOT NULL,
    stem_norm      TEXT    NOT NULL,
    prompt_text    TEXT    NOT NULL DEFAULT '',
    text_hash      TEXT    NOT NULL DEFAULT '',
    encoding       TEXT    NOT NULL DEFAULT 'utf-8',
    file_size      INTEGER NOT NULL DEFAULT 0,
    modified_time  INTEGER NOT NULL DEFAULT 0,
    file_hash      TEXT,
    format         TEXT,
    token_count    INTEGER NOT NULL DEFAULT 0,
    word_count     INTEGER NOT NULL DEFAULT 0,
    section_count  INTEGER NOT NULL DEFAULT 0,
    parse_json     TEXT,
    parse_version  INTEGER NOT NULL DEFAULT 0,
    is_present     INTEGER NOT NULL DEFAULT 1 CHECK (is_present IN (0, 1)),
    is_valid       INTEGER NOT NULL DEFAULT 1 CHECK (is_valid IN (0, 1)),
    created_at     REAL    NOT NULL DEFAULT (unixepoch('subsec')),
    updated_at     REAL    NOT NULL DEFAULT (unixepoch('subsec'))
);
CREATE UNIQUE INDEX ux_prompts_folder_stem ON prompts(folder_id, stem_norm);
CREATE INDEX ix_prompts_text_hash ON prompts(text_hash);
CREATE INDEX ix_prompts_present ON prompts(is_present);
CREATE INDEX ix_prompts_parse_version ON prompts(parse_version);
CREATE INDEX ix_prompts_modified ON prompts(modified_time);

-- FTS5 mit externem Inhalt: der Index verweist auf prompts und dupliziert den
-- Prompttext nicht. Bei hunderttausend Prompts halbiert das die Datenbankgroesse.
CREATE VIRTUAL TABLE prompts_fts USING fts5(
    prompt_text,
    content='prompts',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

-- Der Update-Trigger haengt bewusst an UPDATE OF prompt_text. Metadaten-Updates
-- (Thumbnailstatus, Scanzeit) loesen dadurch keine Neuindizierung des Volltexts aus.
CREATE TRIGGER trg_prompts_fts_ai AFTER INSERT ON prompts BEGIN
    INSERT INTO prompts_fts(rowid, prompt_text) VALUES (new.id, new.prompt_text);
END;
CREATE TRIGGER trg_prompts_fts_ad AFTER DELETE ON prompts BEGIN
    INSERT INTO prompts_fts(prompts_fts, rowid, prompt_text)
    VALUES ('delete', old.id, old.prompt_text);
END;
CREATE TRIGGER trg_prompts_fts_au AFTER UPDATE OF prompt_text ON prompts BEGIN
    INSERT INTO prompts_fts(prompts_fts, rowid, prompt_text)
    VALUES ('delete', old.id, old.prompt_text);
    INSERT INTO prompts_fts(rowid, prompt_text) VALUES (new.id, new.prompt_text);
END;

-- ---------------------------------------------------------------------------
-- Bilder
-- prompt_id ist absichtlich NICHT unique: liegen bild.jpg und bild.png neben
-- einem einzigen bild.txt, sollen beide Bilder den Prompt zeigen. Der Scanner
-- protokolliert diesen Fall als Warnung, verwirft aber keine Datei.
-- ---------------------------------------------------------------------------
CREATE TABLE images (
    id                INTEGER PRIMARY KEY,
    folder_id         INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    prompt_id         INTEGER REFERENCES prompts(id) ON DELETE SET NULL,
    filename          TEXT    NOT NULL,
    full_path         TEXT    NOT NULL,
    stem_norm         TEXT    NOT NULL,
    extension         TEXT    NOT NULL,
    file_size         INTEGER NOT NULL DEFAULT 0,
    modified_time     INTEGER NOT NULL DEFAULT 0,
    file_hash         TEXT,
    width             INTEGER,
    height            INTEGER,
    thumbnail_key     TEXT,
    thumbnail_updated REAL,
    thumbnail_state   INTEGER NOT NULL DEFAULT 0
                      CHECK (thumbnail_state IN (0, 1, 2)),
    is_present        INTEGER NOT NULL DEFAULT 1 CHECK (is_present IN (0, 1)),
    is_valid          INTEGER NOT NULL DEFAULT 1 CHECK (is_valid IN (0, 1)),
    pair_state        TEXT    NOT NULL DEFAULT 'image_only'
                      CHECK (pair_state IN ('complete', 'image_only')),
    created_at        REAL    NOT NULL DEFAULT (unixepoch('subsec')),
    updated_at        REAL    NOT NULL DEFAULT (unixepoch('subsec'))
);
CREATE UNIQUE INDEX ux_images_folder_stem_ext ON images(folder_id, stem_norm, extension);
CREATE INDEX ix_images_folder_present ON images(folder_id, is_present);
CREATE INDEX ix_images_prompt ON images(prompt_id);
CREATE INDEX ix_images_thumb_key ON images(thumbnail_key);
CREATE INDEX ix_images_thumb_state ON images(thumbnail_state) WHERE thumbnail_state = 0;
CREATE INDEX ix_images_pair_state ON images(pair_state);

-- ---------------------------------------------------------------------------
-- Thumbnail-Cacheverwaltung
-- Eigene Tabelle statt eines Boolean in images: Spezifikation 07 fordert drei
-- Groessenstufen, und ein einzelnes Flag kann drei Zustaende nicht abbilden.
-- last_access ist die Grundlage der LRU-Bereinigung mit Speicherobergrenze.
-- ---------------------------------------------------------------------------
CREATE TABLE thumbnails (
    thumbnail_key TEXT    NOT NULL,
    size_px       INTEGER NOT NULL,
    rel_path      TEXT    NOT NULL,
    bytes         INTEGER NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL DEFAULT (unixepoch('subsec')),
    last_access   REAL    NOT NULL DEFAULT (unixepoch('subsec')),
    PRIMARY KEY (thumbnail_key, size_px)
) WITHOUT ROWID;
CREATE INDEX ix_thumbnails_last_access ON thumbnails(last_access);

-- ---------------------------------------------------------------------------
-- Segmente (in den Spezifikationsdateien "tokens")
-- kind ist der entscheidende Zusatz: das Primaerformat dieses Projekts sind
-- Langform-Prosa-Prompts. Dort ist die wiederverwendete Einheit der normalisierte
-- SATZ, nicht das Komma-Tag. Tag-Listen und Bullet-Parameter kommen im selben
-- Prompt daneben vor, deshalb eine Tabelle mit unterschiedenen Arten.
-- Eindeutigkeit ueber token_hash, weil Saetze fuer einen Textindex zu lang sind.
-- ---------------------------------------------------------------------------
CREATE TABLE tokens (
    id          INTEGER PRIMARY KEY,
    kind        TEXT    NOT NULL
                CHECK (kind IN ('sentence', 'tag', 'param', 'header', 'word')),
    token       TEXT    NOT NULL,
    token_hash  TEXT    NOT NULL,
    char_len    INTEGER NOT NULL DEFAULT 0,
    word_len    INTEGER NOT NULL DEFAULT 0,
    sample_text TEXT,
    created_at  REAL    NOT NULL DEFAULT (unixepoch('subsec'))
);
CREATE UNIQUE INDEX ux_tokens_kind_hash ON tokens(kind, token_hash);
CREATE INDEX ix_tokens_kind ON tokens(kind);

-- Nur Zaehler, keine abgeleiteten Werte. Begruendung siehe View token_stats.
CREATE TABLE token_frequency (
    token_id      INTEGER PRIMARY KEY REFERENCES tokens(id) ON DELETE CASCADE,
    doc_count     INTEGER NOT NULL DEFAULT 0,
    total_count   INTEGER NOT NULL DEFAULT 0,
    neg_doc_count INTEGER NOT NULL DEFAULT 0,
    image_count   INTEGER NOT NULL DEFAULT 0,
    updated_at    REAL    NOT NULL DEFAULT (unixepoch('subsec'))
);
CREATE INDEX ix_token_frequency_doc_count ON token_frequency(doc_count DESC);

-- ---------------------------------------------------------------------------
-- Promptstruktur
-- prompt_sections gibt es in keiner Spezifikationsdatei, ist fuer das
-- tatsaechliche Promptformat aber notwendig: die CAPS-Ueberschriften
-- ("CAMERA & LENS SETTINGS", "NEGATIVE PROMPT") bestimmen, welche Segmentart
-- folgt und ob die Segmente negativ sind.
-- ---------------------------------------------------------------------------
CREATE TABLE prompt_sections (
    id         INTEGER PRIMARY KEY,
    prompt_id  INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    ordinal    INTEGER NOT NULL,
    kind       TEXT    NOT NULL
               CHECK (kind IN ('prose', 'tag_list', 'params', 'header', 'unknown')),
    label      TEXT,
    label_norm TEXT,
    negative   INTEGER NOT NULL DEFAULT 0 CHECK (negative IN (0, 1)),
    char_start INTEGER NOT NULL DEFAULT 0,
    char_end   INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX ux_prompt_sections ON prompt_sections(prompt_id, ordinal);
CREATE INDEX ix_prompt_sections_label ON prompt_sections(label_norm);

-- char_start/char_end sind Zusatzfelder gegenueber Spezifikation 04. Ohne sie
-- laesst sich ein gefundenes Segment nicht auf seine Stelle im Originaltext
-- zurueckbilden, und die tokenbasierte Hervorhebung der Vergleichsansicht
-- (Spezifikation 01) ist nicht umsetzbar.
CREATE TABLE prompt_tokens (
    id         INTEGER PRIMARY KEY,
    prompt_id  INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    token_id   INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    section_id INTEGER REFERENCES prompt_sections(id) ON DELETE SET NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    weight     REAL    NOT NULL DEFAULT 1.0,
    negative   INTEGER NOT NULL DEFAULT 0 CHECK (negative IN (0, 1)),
    char_start INTEGER NOT NULL DEFAULT 0,
    char_end   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_prompt_tokens_prompt ON prompt_tokens(prompt_id, position);
CREATE INDEX ix_prompt_tokens_token ON prompt_tokens(token_id, negative);

-- Generische Parameter statt A1111-fester Spalten: das Beispielformat liefert
-- "Camera", "Lens", "Aperture", "Shutter Speed", "ISO", "White Balance", "Focus",
-- "Depth of Field", "Aspect Ratio". Ein festes Spaltenschema wuerde jedes neue
-- Feld zur Migration machen.
CREATE TABLE prompt_params (
    id         INTEGER PRIMARY KEY,
    prompt_id  INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    section_id INTEGER REFERENCES prompt_sections(id) ON DELETE SET NULL,
    key_norm   TEXT    NOT NULL,
    key_raw    TEXT    NOT NULL,
    value_text TEXT    NOT NULL,
    value_num  REAL,
    unit       TEXT,
    char_start INTEGER NOT NULL DEFAULT 0,
    char_end   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_prompt_params_prompt ON prompt_params(prompt_id);
CREATE INDEX ix_prompt_params_key ON prompt_params(key_norm, value_text);

-- ---------------------------------------------------------------------------
-- Analyseergebnisse
-- prompt_clusters loest das Fremdschluesselproblem aus Spezifikation 03: ein
-- Basisprompt als Schnittmenge einer Familie existiert in der Regel als kein
-- realer Prompt. base_signature haelt die abstrakte Basis,
-- representative_prompt_id den echten Prompt, der ihr am naechsten liegt.
-- ---------------------------------------------------------------------------
CREATE TABLE prompt_clusters (
    id                      INTEGER PRIMARY KEY,
    representative_prompt_id INTEGER REFERENCES prompts(id) ON DELETE SET NULL,
    base_signature          TEXT,
    label                   TEXT,
    member_count            INTEGER NOT NULL DEFAULT 0,
    image_count             INTEGER NOT NULL DEFAULT 0,
    avg_similarity          REAL,
    algo_version            INTEGER NOT NULL DEFAULT 1,
    generated_at            REAL    NOT NULL DEFAULT (unixepoch('subsec'))
);
CREATE INDEX ix_prompt_clusters_rep ON prompt_clusters(representative_prompt_id);

CREATE TABLE cluster_members (
    cluster_id         INTEGER NOT NULL REFERENCES prompt_clusters(id) ON DELETE CASCADE,
    prompt_id          INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    similarity_to_base REAL,
    PRIMARY KEY (cluster_id, prompt_id)
) WITHOUT ROWID;
CREATE INDEX ix_cluster_members_prompt ON cluster_members(prompt_id);

CREATE TABLE prompt_variants (
    id                INTEGER PRIMARY KEY,
    base_prompt_id    INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    variant_prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    cluster_id        INTEGER REFERENCES prompt_clusters(id) ON DELETE CASCADE,
    similarity        REAL    NOT NULL DEFAULT 0,
    added_count       INTEGER NOT NULL DEFAULT 0,
    removed_count     INTEGER NOT NULL DEFAULT 0,
    changed_count     INTEGER NOT NULL DEFAULT 0,
    diff_json         TEXT,
    algo_version      INTEGER NOT NULL DEFAULT 1,
    generated_at      REAL    NOT NULL DEFAULT (unixepoch('subsec')),
    CHECK (base_prompt_id <> variant_prompt_id)
);
CREATE UNIQUE INDEX ux_prompt_variants ON prompt_variants(base_prompt_id, variant_prompt_id);
CREATE INDEX ix_prompt_variants_variant ON prompt_variants(variant_prompt_id);
CREATE INDEX ix_prompt_variants_cluster ON prompt_variants(cluster_id);

-- Nur die k naechsten Nachbarn pro Prompt. Alle Paare waeren bei hunderttausend
-- Prompts fuenf Milliarden Zeilen; mit rank als Teil des Primaerschluessels ist
-- der Speicherbedarf durch k begrenzt.
CREATE TABLE prompt_neighbors (
    prompt_id   INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    rank        INTEGER NOT NULL,
    neighbor_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    similarity  REAL    NOT NULL,
    PRIMARY KEY (prompt_id, rank)
) WITHOUT ROWID;
CREATE INDEX ix_prompt_neighbors_neighbor ON prompt_neighbors(neighbor_id);

-- cluster_id statt base_prompt_id als Schluessel: ein Evolutionsbaum deckt eine
-- ganze Familie ab, nicht einen einzelnen Prompt.
CREATE TABLE evolution_cache (
    id             INTEGER PRIMARY KEY,
    cluster_id     INTEGER NOT NULL REFERENCES prompt_clusters(id) ON DELETE CASCADE,
    base_prompt_id INTEGER REFERENCES prompts(id) ON DELETE SET NULL,
    tree_json      TEXT    NOT NULL,
    node_count     INTEGER NOT NULL DEFAULT 0,
    generated_at   REAL    NOT NULL DEFAULT (unixepoch('subsec')),
    version        INTEGER NOT NULL DEFAULT 1,
    algo_version   INTEGER NOT NULL DEFAULT 1,
    dirty          INTEGER NOT NULL DEFAULT 0 CHECK (dirty IN (0, 1))
);
CREATE UNIQUE INDEX ux_evolution_cluster ON evolution_cache(cluster_id);
CREATE INDEX ix_evolution_dirty ON evolution_cache(dirty) WHERE dirty = 1;

-- ---------------------------------------------------------------------------
-- Arbeitsvorrat und Pipelinezustand
-- Dirty-Flags als Tabelle statt als Spalte: Jobs holen ihren Vorrat atomar mit
-- DELETE ... RETURNING, und ein Versionssprung in analysis_state invalidiert
-- eine Stufe komplett, ohne Millionen Flags zu schreiben.
-- ---------------------------------------------------------------------------
CREATE TABLE dirty_prompts (
    prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    stage     TEXT    NOT NULL CHECK (stage IN ('parse', 'stats', 'similarity', 'cluster', 'evolution')),
    reason    TEXT,
    marked_at REAL    NOT NULL DEFAULT (unixepoch('subsec')),
    attempts  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (prompt_id, stage)
) WITHOUT ROWID;
CREATE INDEX ix_dirty_stage ON dirty_prompts(stage, marked_at);

CREATE TABLE analysis_state (
    stage         TEXT    PRIMARY KEY,
    algo_version  INTEGER NOT NULL DEFAULT 1,
    status        TEXT    NOT NULL DEFAULT 'idle',
    last_run      REAL,
    last_duration REAL,
    processed     INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

-- ---------------------------------------------------------------------------
-- Betrieb
-- ---------------------------------------------------------------------------
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    value_type TEXT NOT NULL DEFAULT 'str',
    updated_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
) WITHOUT ROWID;

CREATE TABLE library_stats (
    key        TEXT    PRIMARY KEY,
    int_value  INTEGER NOT NULL DEFAULT 0,
    real_value REAL,
    updated_at REAL    NOT NULL DEFAULT (unixepoch('subsec'))
) WITHOUT ROWID;

CREATE TABLE scan_history (
    id            INTEGER PRIMARY KEY,
    scan_time     REAL    NOT NULL DEFAULT (unixepoch('subsec')),
    duration      REAL,
    scan_type     TEXT    NOT NULL DEFAULT 'incremental',
    folder_id     INTEGER,
    new_files     INTEGER NOT NULL DEFAULT 0,
    changed_files INTEGER NOT NULL DEFAULT 0,
    deleted_files INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'ok',
    note          TEXT
);
CREATE INDEX ix_scan_history_time ON scan_history(scan_time DESC);

-- count/last_seen aggregieren gleichartige Fehler. Ohne das schreibt ein
-- NAS-Ausfall waehrend eines Scans zehntausende identische Zeilen.
CREATE TABLE error_log (
    id          INTEGER PRIMARY KEY,
    timestamp   REAL    NOT NULL DEFAULT (unixepoch('subsec')),
    last_seen   REAL    NOT NULL DEFAULT (unixepoch('subsec')),
    severity    INTEGER NOT NULL,
    source      TEXT    NOT NULL,
    message_key TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    details     TEXT,
    count       INTEGER NOT NULL DEFAULT 1,
    resolved    INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1))
);
CREATE UNIQUE INDEX ux_error_log_dedupe ON error_log(severity, source, message_key, resolved);
CREATE INDEX ix_error_log_last_seen ON error_log(last_seen DESC);
CREATE INDEX ix_error_log_open ON error_log(resolved, severity) WHERE resolved = 0;

CREATE TABLE system_status (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
) WITHOUT ROWID;

CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
    duration   REAL
);

-- ---------------------------------------------------------------------------
-- Abgeleitete Sichten
-- ---------------------------------------------------------------------------

-- commonness  = Anteil der Prompts, die das Segment enthalten -> Basisprompterkennung
-- idf         = Unterscheidungskraft des Segments             -> Editorhervorhebung,
--                                                                Similarity-Gewichtung
-- Beide haengen von der Gesamtzahl ab und werden deshalb NICHT persistiert.
CREATE VIEW token_stats AS
SELECT
    t.id                        AS token_id,
    t.kind                      AS kind,
    t.token                     AS token,
    t.sample_text               AS sample_text,
    COALESCE(f.doc_count, 0)    AS doc_count,
    COALESCE(f.total_count, 0)  AS total_count,
    COALESCE(f.neg_doc_count, 0) AS neg_doc_count,
    CAST(COALESCE(f.doc_count, 0) AS REAL)
        / MAX(1, (SELECT int_value FROM library_stats WHERE key = 'distinct_prompt_texts'))
                                AS commonness,
    LN(
        CAST(MAX(1, (SELECT int_value FROM library_stats WHERE key = 'distinct_prompt_texts')) AS REAL)
        / MAX(1, COALESCE(f.doc_count, 0))
    )                           AS idf
FROM tokens t
LEFT JOIN token_frequency f ON f.token_id = t.id;

-- Grundprinzip "kein Bild ohne Prompt" bleibt Leitbild, aber Verstoesse werden
-- sichtbar gemacht statt verworfen (Spezifikation 08 verlangt beides).
CREATE VIEW v_orphans AS
SELECT 'image_only' AS kind, i.id AS id, i.full_path AS path, i.folder_id AS folder_id
FROM images i
WHERE i.is_present = 1 AND i.prompt_id IS NULL
UNION ALL
SELECT 'prompt_only' AS kind, p.id AS id, p.full_path AS path, p.folder_id AS folder_id
FROM prompts p
WHERE p.is_present = 1
  AND NOT EXISTS (SELECT 1 FROM images i WHERE i.prompt_id = p.id AND i.is_present = 1);
"""

SEED = """
INSERT INTO library_stats(key, int_value) VALUES
    ('image_count', 0),
    ('prompt_count', 0),
    ('distinct_prompt_texts', 0),
    ('token_count', 0),
    ('cluster_count', 0);

INSERT INTO analysis_state(stage, algo_version, status) VALUES
    ('parse', 1, 'idle'),
    ('stats', 1, 'idle'),
    ('similarity', 1, 'idle'),
    ('cluster', 1, 'idle'),
    ('evolution', 1, 'idle');

INSERT INTO system_status(key, value) VALUES
    ('nas_reachable', 'unknown'),
    ('index_status', 'empty'),
    ('last_successful_scan', ''),
    ('last_analysis', ''),
    ('last_evolution', '');
"""


def up(conn: sqlite3.Connection) -> None:
    """Laeuft innerhalb der Transaktion des Aufrufers, siehe app/database/sqlscript.py."""
    exec_script(conn, SCHEMA)
    exec_script(conn, SEED)
