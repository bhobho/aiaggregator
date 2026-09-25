"""Database upkeep (maintenance.py) and the search-index triggers."""
from datetime import datetime, timedelta, timezone

from aiaggregator import db, maintenance, queries
from aiaggregator.models import Article, Source


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _add(conn, source_id, guid, *, days_ago, status="enriched", content="<p>body</p>",
         title="Some AI story"):
    db.insert_article(conn, Article(
        source_id=source_id, guid=guid, url=f"https://example.com/{guid}", title=title,
        content_hash=guid, published_at=_iso(days_ago), fetched_at=_iso(days_ago),
        raw_summary="raw", content=content))
    aid = conn.execute("SELECT id FROM articles WHERE guid=?", (guid,)).fetchone()[0]
    conn.execute("UPDATE articles SET status=?, embedding=? WHERE id=?",
                 (status, b"\x00" * 16, aid))
    conn.commit()
    return aid


def _row(conn, aid):
    return conn.execute("SELECT status, content, embedding FROM articles WHERE id=?",
                        (aid,)).fetchone()


def _fts_consistent(conn):
    # rank=1 compares every index entry with the articles table; raises if they differ.
    conn.execute("INSERT INTO articles_fts(articles_fts, rank) VALUES('integrity-check', 1)")
    conn.commit()   # the check is an INSERT: don't leave a write transaction open


def _hits(conn, q):
    return conn.execute("SELECT count(*) FROM articles_fts WHERE articles_fts MATCH ?",
                        (q,)).fetchone()[0]


def test_run_prunes_slims_and_protects_owner(tmp_path, conn, source_id):
    own = db.upsert_source(conn, Source(name=queries.MY_MEDIUM_SOURCE,
                                        url="https://medium.com/feed/me", category="blog"))
    old_failed = _add(conn, source_id, "old-failed", days_ago=40, status="failed")
    new_failed = _add(conn, source_id, "new-failed", days_ago=5, status="failed")
    ancient = _add(conn, source_id, "ancient", days_ago=100)
    mid = _add(conn, source_id, "mid", days_ago=20)
    fresh = _add(conn, source_id, "fresh", days_ago=3)
    own_failed = _add(conn, own, "own-failed", days_ago=40, status="failed")
    own_ancient = _add(conn, own, "own-ancient", days_ago=100)

    res = maintenance.run(tmp_path / "test.db")

    assert _row(conn, old_failed) is None                        # deleted
    assert _row(conn, new_failed) is not None                    # too recent to delete
    assert _row(conn, ancient)["content"] is None                # full text dropped...
    assert _row(conn, ancient)["status"] == "enriched"           # ...row kept
    assert _row(conn, mid)["content"] == "<p>body</p>"           # under content age
    assert _row(conn, mid)["embedding"] is None                  # past clustering window
    assert _row(conn, fresh)["embedding"] is not None            # still used by clustering
    assert _row(conn, own_failed) is not None                    # owner posts never deleted
    assert _row(conn, own_ancient)["content"] == "<p>body</p>"   # ...or slimmed
    assert res["failed_deleted"] == 1 and res["content_cleared"] == 1
    assert res["search_index"] == "ok"
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2  # switched to incremental
    _fts_consistent(conn)

    assert maintenance.run(tmp_path / "test.db")["space"] == "incremental vacuum"


def test_run_rebuilds_a_drifted_search_index(tmp_path, conn, source_id):
    _add(conn, source_id, "real", days_ago=1, title="Genuine headline")
    conn.execute("INSERT INTO articles_fts(rowid, title, summary, tags) "
                 "VALUES (99999, 'ghost entry', NULL, NULL)")    # entry with no article
    conn.commit()
    assert _hits(conn, "ghost") == 1

    assert maintenance.run(tmp_path / "test.db")["search_index"] == "rebuilt"
    assert _hits(conn, "ghost") == 0 and _hits(conn, "genuine") == 1
    _fts_consistent(conn)


def test_update_trigger_only_fires_on_indexed_columns(conn, source_id):
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='articles_au'").fetchone()[0]
    assert "UPDATE OF title, summary, tags" in sql
    aid = _add(conn, source_id, "t1", days_ago=1, title="Quantum widgets")
    conn.execute("UPDATE articles SET cluster_id=7, image_url='x', status='failed' WHERE id=?",
                 (aid,))
    conn.execute("UPDATE articles SET title='Photonic gadgets' WHERE id=?", (aid,))
    conn.commit()
    assert _hits(conn, "photonic") == 1 and _hits(conn, "quantum") == 0
    _fts_consistent(conn)


def test_init_db_replaces_old_triggers_and_reindexes(conn, source_id):
    # The original triggers indexed COALESCE(summary, raw_summary) and fired on any update.
    for name in ("articles_ai", "articles_ad", "articles_au"):
        conn.execute(f"DROP TRIGGER {name}")
    conn.executescript("""
        CREATE TRIGGER articles_ai AFTER INSERT ON articles BEGIN
            INSERT INTO articles_fts(rowid, title, summary, tags)
            VALUES (new.id, new.title, COALESCE(new.summary, new.raw_summary), COALESCE(new.tags, ''));
        END;
        CREATE TRIGGER articles_ad AFTER DELETE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, summary, tags)
            VALUES('delete', old.id, old.title, COALESCE(old.summary, old.raw_summary), COALESCE(old.tags, ''));
        END;
        CREATE TRIGGER articles_au AFTER UPDATE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, summary, tags)
            VALUES('delete', old.id, old.title, COALESCE(old.summary, old.raw_summary), COALESCE(old.tags, ''));
            INSERT INTO articles_fts(rowid, title, summary, tags)
            VALUES (new.id, new.title, COALESCE(new.summary, new.raw_summary), COALESCE(new.tags, ''));
        END;""")
    _add(conn, source_id, "legacy", days_ago=1, title="Legacy row")   # indexed the old way

    db.init_db(conn)

    for name in ("articles_ai", "articles_ad", "articles_au"):
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
        assert "raw_summary" not in sql
    _fts_consistent(conn)                                            # re-indexed
    assert _hits(conn, "legacy") == 1
