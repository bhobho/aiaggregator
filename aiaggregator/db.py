"""SQLite storage layer (stdlib sqlite3, with FTS5 search)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .config import settings
from .models import Article, Source, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    url           TEXT NOT NULL UNIQUE,
    category      TEXT NOT NULL,
    company       TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    etag          TEXT,
    last_modified TEXT,
    last_fetch    TEXT,
    last_status   TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    guid         TEXT NOT NULL,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    author       TEXT,
    published_at TEXT,
    fetched_at   TEXT NOT NULL,
    raw_summary  TEXT,
    content_hash TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'new',
    summary      TEXT,
    tags         TEXT,
    companies    TEXT,
    importance   INTEGER,
    cluster_id   INTEGER,
    UNIQUE(source_id, guid)
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_cluster ON articles(cluster_id);

CREATE TABLE IF NOT EXISTS clusters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT,
    top_article_id  INTEGER,
    size            INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL UNIQUE,
    markdown   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title, summary, tags, content='articles', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, summary, tags)
    VALUES (new.id, new.title, COALESCE(new.summary, new.raw_summary), COALESCE(new.tags, ''));
END;
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, summary, tags)
    VALUES('delete', old.id, old.title, COALESCE(old.summary, old.raw_summary), COALESCE(old.tags, ''));
END;
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, summary, tags)
    VALUES('delete', old.id, old.title, COALESCE(old.summary, old.raw_summary), COALESCE(old.tags, ''));
    INSERT INTO articles_fts(rowid, title, summary, tags)
    VALUES (new.id, new.title, COALESCE(new.summary, new.raw_summary), COALESCE(new.tags, ''));
END;
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ----- sources ---------------------------------------------------------------

def upsert_source(conn: sqlite3.Connection, src: Source) -> int:
    cur = conn.execute(
        """INSERT INTO sources (name, url, category, company, active)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
               name=excluded.name, category=excluded.category,
               company=excluded.company, active=excluded.active
           RETURNING id""",
        (src.name, src.url, src.category, src.company, int(src.active)),
    )
    sid = cur.fetchone()["id"]
    conn.commit()
    return sid


def prune_sources(conn: sqlite3.Connection, keep_urls: set[str]) -> int:
    """Delete sources (and their articles) whose url is not in keep_urls."""
    rows = conn.execute("SELECT id, url FROM sources").fetchall()
    removed = 0
    for r in rows:
        if r["url"] not in keep_urls:
            conn.execute("DELETE FROM articles WHERE source_id=?", (r["id"],))
            conn.execute("DELETE FROM sources WHERE id=?", (r["id"],))
            removed += 1
    conn.commit()
    return removed


def list_sources(conn: sqlite3.Connection, active_only: bool = True) -> list[Source]:
    q = "SELECT * FROM sources"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY category, name"
    out = []
    for r in conn.execute(q):
        out.append(
            Source(
                id=r["id"], name=r["name"], url=r["url"], category=r["category"],
                company=r["company"], active=bool(r["active"]), etag=r["etag"],
                last_modified=r["last_modified"], last_fetch=r["last_fetch"],
                last_status=r["last_status"],
            )
        )
    return out


def update_source_fetch(conn: sqlite3.Connection, source_id: int, *, etag: str | None,
                        last_modified: str | None, status: str) -> None:
    conn.execute(
        "UPDATE sources SET etag=?, last_modified=?, last_fetch=?, last_status=? WHERE id=?",
        (etag, last_modified, now_iso(), status, source_id),
    )
    conn.commit()


def source_counts(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute("SELECT source_id, COUNT(*) c FROM articles GROUP BY source_id")
    return {r["source_id"]: r["c"] for r in rows}


# ----- articles --------------------------------------------------------------

def insert_article(conn: sqlite3.Connection, a: Article) -> int | None:
    """Insert if new; returns row id or None if it already existed (deduped)."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO articles
           (source_id, guid, url, title, author, published_at, fetched_at,
            raw_summary, content_hash, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')""",
        (a.source_id, a.guid, a.url, a.title, a.author, a.published_at,
         a.fetched_at or now_iso(), a.raw_summary, a.content_hash),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def pending_enrichment(conn: sqlite3.Connection, limit: int) -> list[Article]:
    rows = conn.execute(
        "SELECT * FROM articles WHERE status='new' ORDER BY fetched_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [Article.from_row(r) for r in rows]


def save_enrichment(conn: sqlite3.Connection, article_id: int, *, summary: str,
                    tags: list[str], companies: list[str], importance: int) -> None:
    conn.execute(
        """UPDATE articles SET status='enriched', summary=?, tags=?, companies=?,
           importance=? WHERE id=?""",
        (summary, json.dumps(tags), json.dumps(companies), importance, article_id),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, article_id: int) -> None:
    conn.execute("UPDATE articles SET status='failed' WHERE id=?", (article_id,))
    conn.commit()


def set_cluster(conn: sqlite3.Connection, article_id: int, cluster_id: int) -> None:
    conn.execute("UPDATE articles SET cluster_id=? WHERE id=?", (cluster_id, article_id))
    conn.commit()


def top_enriched(conn: sqlite3.Connection, days: int, limit: int) -> list[Article]:
    """Top enriched articles ingested in the last `days`, by importance.

    Uses fetched_at (when the item entered the system) rather than published_at,
    so a blog post with an older publish date still surfaces in the day's digest."""
    rows = conn.execute(
        """SELECT * FROM articles
           WHERE status='enriched' AND fetched_at >= datetime('now', ?)
           ORDER BY importance DESC, fetched_at DESC LIMIT ?""",
        (f"-{days} days", limit * 3),
    ).fetchall()
    return [Article.from_row(r) for r in rows]


def cluster_sizes(conn: sqlite3.Connection) -> dict[int, int]:
    return {r["id"]: r["size"] for r in conn.execute("SELECT id, size FROM clusters")}


def recent_articles(conn: sqlite3.Connection, days: int) -> list[Article]:
    rows = conn.execute(
        """SELECT * FROM articles
           WHERE COALESCE(published_at, fetched_at) >= datetime('now', ?)
           ORDER BY COALESCE(published_at, fetched_at) DESC""",
        (f"-{days} days",),
    ).fetchall()
    return [Article.from_row(r) for r in rows]
