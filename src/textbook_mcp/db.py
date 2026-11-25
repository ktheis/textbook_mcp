"""SQLite + FTS5 schema and connection helpers for the Chemistry 2e knowledge base."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "chemistry2e.db"

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS chapters (
    number INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    intro_module_id TEXT,
    section_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sections (
    id TEXT PRIMARY KEY,              -- e.g. '1.1', '1.0' (intro), 'app-A'
    chapter_num INTEGER,              -- NULL for appendices/preface
    section_num TEXT NOT NULL,        -- e.g. '1.1', 'A'
    title TEXT NOT NULL,
    module_id TEXT NOT NULL,
    kind TEXT NOT NULL,               -- 'intro', 'section', 'appendix', 'preface'
    learning_objectives TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    heading TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    char_count INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    section_id UNINDEXED,
    heading,
    content,
    content='chunks',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS figures (
    id TEXT PRIMARY KEY,              -- CNXML figure id
    section_id TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    chapter_num INTEGER,
    label TEXT NOT NULL,              -- e.g. 'Figure 1.2'
    caption TEXT NOT NULL DEFAULT '',
    alt_text TEXT NOT NULL DEFAULT '',
    media_path TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS book_tables (
    id TEXT PRIMARY KEY,
    section_id TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    chapter_num INTEGER,
    label TEXT NOT NULL,              -- e.g. 'Table 1.1'
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS examples (
    id TEXT PRIMARY KEY,
    section_id TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    chapter_num INTEGER,
    label TEXT NOT NULL,              -- e.g. 'Example 1.1'
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exercises (
    id TEXT PRIMARY KEY,              -- canonical e.g. 'Exercise 1.15'
    chapter_num INTEGER NOT NULL,
    exercise_num INTEGER NOT NULL,
    section_id TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    problem_text TEXT NOT NULL,
    answer_text TEXT,                 -- NULL when even-numbered / unavailable
    has_answer INTEGER NOT NULL       -- 1 if official answer key exists, 0 otherwise
);

CREATE VIRTUAL TABLE IF NOT EXISTS exercises_fts USING fts5(
    id UNINDEXED,
    section_id UNINDEXED,
    problem_text,
    content='exercises',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS reference_tables (
    id TEXT PRIMARY KEY,              -- e.g. 'app-G:table-1'
    appendix_id TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,           -- e.g. 'thermodynamics', 'equilibrium', 'constants', 'periodic_table', 'general'
    content TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS reference_fts USING fts5(
    id UNINDEXED,
    title,
    category,
    content,
    content='reference_tables',
    content_rowid='rowid',
    tokenize='porter unicode61'
);
"""


def get_connection(db_path: Path | str = DEFAULT_DB_PATH, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection with Row factory enabled."""
    path = Path(db_path)
    if read_only:
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and FTS5 indexes."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild FTS5 indexes from their backing content tables."""
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO exercises_fts(exercises_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO reference_fts(reference_fts) VALUES('rebuild')")
    conn.commit()
