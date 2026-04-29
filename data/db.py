"""
SQLite helpers for the cyber-news-agent pipeline.
Schema : feeds, items, articles, publications.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "articles.db"


@contextmanager
def get_conn(path: Path = DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: Path = DB_PATH) -> None:
    with get_conn(path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS feeds (
                id          INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                url         TEXT NOT NULL UNIQUE,
                last_fetch  TEXT
            );

            CREATE TABLE IF NOT EXISTS items (
                id           INTEGER PRIMARY KEY,
                feed_id      INTEGER REFERENCES feeds(id),
                url_hash     TEXT NOT NULL UNIQUE,
                url          TEXT NOT NULL,
                title        TEXT,
                summary      TEXT,
                published_at TEXT,
                fetched_at   TEXT NOT NULL DEFAULT (datetime('now')),
                score        REAL,
                score_reason TEXT,
                selected     INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS articles (
                id           INTEGER PRIMARY KEY,
                item_id      INTEGER REFERENCES items(id),
                slug         TEXT NOT NULL UNIQUE,
                title        TEXT NOT NULL,
                lead         TEXT,
                body_md      TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'draft',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS publications (
                id           INTEGER PRIMARY KEY,
                article_id   INTEGER REFERENCES articles(id),
                channel      TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                url          TEXT,
                payload      TEXT,
                published_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_items_score    ON items(score DESC);
            CREATE INDEX IF NOT EXISTS idx_items_selected ON items(selected);
            CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
        """)
