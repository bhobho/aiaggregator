"""Read queries that power the dashboard (filters, search, grouping)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from . import db, ranking
from .models import Article


@dataclass
class FeedFilters:
    company: str | None = None
    category: str | None = None
    days: int | None = None         # time window
    min_importance: int | None = None
    search: str | None = None
    sort: str = "newest"            # newest | importance
    limit: int = 100


def _fts_match(term: str) -> str:
    # Sanitize into a safe FTS5 prefix query.
    words = [w for w in "".join(c if c.isalnum() else " " for c in term).split() if w]
    return " ".join(f"{w}*" for w in words)


def feed(conn: sqlite3.Connection, f: FeedFilters) -> list[Article]:
    where = ["s.active = 1"]
    params: list = []

    if f.search:
        match = _fts_match(f.search)
        if match:
            where.append(
                "a.id IN (SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?)"
            )
            params.append(match)

    if f.category:
        where.append("s.category = ?")
        params.append(f.category)
    if f.company:
        where.append("(s.company = ? OR a.companies LIKE ?)")
        params.extend([f.company, f'%"{f.company}"%'])
    if f.min_importance is not None:
        where.append("COALESCE(a.importance, 0) >= ?")
        params.append(f.min_importance)
    if f.days is not None:
        where.append("COALESCE(a.published_at, a.fetched_at) >= datetime('now', ?)")
        params.append(f"-{f.days} days")

    where_sql = " AND ".join(where)

    if f.sort == "importance":
        # Fetch a candidate pool by raw importance, then re-rank by composite score.
        sql = f"""
            SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
            WHERE {where_sql}
            ORDER BY COALESCE(a.importance, 0) DESC,
                     COALESCE(a.published_at, a.fetched_at) DESC
            LIMIT ?
        """
        params.append(max(f.limit * 4, 200))
        rows = conn.execute(sql, params).fetchall()
        ranked = rank_articles(conn, [Article.from_row(r) for r in rows])
        return ranked[: f.limit]

    sql = f"""
        SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
        WHERE {where_sql}
        ORDER BY COALESCE(a.published_at, a.fetched_at) DESC
        LIMIT ?
    """
    params.append(f.limit)
    rows = conn.execute(sql, params).fetchall()
    return [Article.from_row(r) for r in rows]


def rank_articles(conn: sqlite3.Connection, articles: list[Article]) -> list[Article]:
    """Sort articles by the composite rank score (see ranking.py)."""
    srcmap = source_name_map(conn)
    sizes = db.cluster_sizes(conn)
    now = datetime.now(timezone.utc)

    def key(a: Article) -> float:
        category = srcmap.get(a.source_id, ("", ""))[1]
        csize = sizes.get(a.cluster_id, 1) if a.cluster_id else 1
        return ranking.rank_score(a, category, csize, now)

    return sorted(articles, key=key, reverse=True)


def ranked_enriched(conn: sqlite3.Connection, days: int, limit: int) -> list[Article]:
    """Top enriched articles within a window, ordered by composite rank."""
    rows = conn.execute(
        """SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
           WHERE s.active = 1 AND a.status = 'enriched'
                 AND a.fetched_at >= datetime('now', ?)
           ORDER BY COALESCE(a.importance, 0) DESC, a.fetched_at DESC
           LIMIT ?""",
        (f"-{days} days", limit * 5),
    ).fetchall()
    return rank_articles(conn, [Article.from_row(r) for r in rows])[:limit]


def source_name_map(conn: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    return {
        r["id"]: (r["name"], r["category"])
        for r in conn.execute("SELECT id, name, category FROM sources")
    }


def group_clusters(articles: list[Article]) -> list[dict]:
    """Collapse clustered articles: lead article + extras. Singletons pass through."""
    groups: dict[int, dict] = {}
    out: list[dict] = []
    for a in articles:
        if a.cluster_id is None:
            out.append({"lead": a, "extras": []})
            continue
        g = groups.get(a.cluster_id)
        if g is None:
            # first occurrence is the best-ranked variant (input is rank-sorted) -> lead
            g = {"lead": a, "extras": []}
            groups[a.cluster_id] = g
            out.append(g)
        else:
            g["extras"].append(a)
    return out


def top_headlines(conn: sqlite3.Connection, limit: int = 8) -> list[Article]:
    """Top headlines for the sidebar: enriched articles by composite rank, with
    recent items filling in if there aren't enough enriched yet."""
    rows = conn.execute(
        """SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
           WHERE s.active = 1 AND a.status = 'enriched'
           ORDER BY COALESCE(a.importance, 0) DESC, COALESCE(a.published_at, a.fetched_at) DESC
           LIMIT ?""",
        (limit * 4,),
    ).fetchall()
    arts = rank_articles(conn, [Article.from_row(r) for r in rows])[:limit]
    if len(arts) < limit:
        seen = {a.id for a in arts}
        extra = conn.execute(
            """SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
               WHERE s.active = 1
               ORDER BY COALESCE(a.published_at, a.fetched_at) DESC LIMIT ?""",
            (limit * 2,),
        ).fetchall()
        for r in extra:
            a = Article.from_row(r)
            if a.id not in seen:
                arts.append(a)
            if len(arts) >= limit:
                break
    return arts[:limit]


def companies(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT company FROM sources WHERE company IS NOT NULL ORDER BY company"
    )
    return [r["company"] for r in rows]


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """SELECT
             COUNT(*) total,
             SUM(status='enriched') enriched,
             SUM(status='new') pending,
             SUM(status='failed') failed
           FROM articles"""
    ).fetchone()
    clusters = conn.execute("SELECT COUNT(*) c FROM clusters").fetchone()["c"]
    return {
        "total": row["total"] or 0,
        "enriched": row["enriched"] or 0,
        "pending": row["pending"] or 0,
        "failed": row["failed"] or 0,
        "clusters": clusters,
    }
