"""Schema Version 2 - Korrektur der View token_stats und Indizes fuer die Segmentsuche.

Zwei Aenderungen:

1. Der Nenner von commonness und idf war distinct_prompt_texts, waehrend
   doc_count Prompt-Zeilen zaehlt. Bei einer Bibliothek mit vielen identischen
   Prompts (Batch-Generierungen) liefen Zaehler und Nenner auseinander:
   commonness konnte deutlich ueber 1 steigen und idf negativ werden.

   Der Nenner ist jetzt prompt_count. Zaehler und Nenner beziehen sich damit auf
   dieselbe Grundgesamtheit, und ein Prompt, der fuenfzig Mal identisch
   vorkommt, hebt Zaehler und Nenner gleichermassen.

2. Zusaetzliche Indizes, die erst mit der Segmentsuche gebraucht werden:
   der Weg vom Segment zu den Bildern fuehrt ueber prompt_tokens und images.
"""

from __future__ import annotations

import sqlite3

from app.database.sqlscript import exec_script

VERSION = 2
NAME = "token_stats_denominator"

SCHEMA = """
DROP VIEW IF EXISTS token_stats;

CREATE VIEW token_stats AS
SELECT
    t.id                         AS token_id,
    t.kind                       AS kind,
    t.token                      AS token,
    t.sample_text                AS sample_text,
    t.word_len                   AS word_len,
    COALESCE(f.doc_count, 0)     AS doc_count,
    COALESCE(f.total_count, 0)   AS total_count,
    COALESCE(f.neg_doc_count, 0) AS neg_doc_count,
    CAST(COALESCE(f.doc_count, 0) AS REAL)
        / MAX(1, (SELECT int_value FROM library_stats WHERE key = 'prompt_count'))
                                 AS commonness,
    LN(
        CAST(MAX(1, (SELECT int_value FROM library_stats WHERE key = 'prompt_count')) AS REAL)
        / MAX(1, COALESCE(f.doc_count, 0))
    )                            AS idf
FROM tokens t
LEFT JOIN token_frequency f ON f.token_id = t.id;

CREATE INDEX IF NOT EXISTS ix_prompt_tokens_lookup
    ON prompt_tokens(token_id, prompt_id);
CREATE INDEX IF NOT EXISTS ix_prompt_tokens_section
    ON prompt_tokens(section_id);
CREATE INDEX IF NOT EXISTS ix_tokens_hash
    ON tokens(token_hash);
"""


def up(conn: sqlite3.Connection) -> None:
    exec_script(conn, SCHEMA)
