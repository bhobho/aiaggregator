"""Group near-duplicate stories across sources.

Uses a pure-Python TF-IDF cosine over article titles+summaries (no heavy deps).
Greedy single-pass clustering over a recent window.
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
from collections import Counter

from .. import db
from ..config import settings
from ..models import now_iso

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "is",
    "are", "as", "at", "by", "from", "that", "this", "it", "its", "be", "new",
    "ai", "model", "models",
}


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP and len(w) > 2]


def _tfidf_vectors(docs: list[str]) -> list[dict[str, float]]:
    tokenized = [_tokens(d) for d in docs]
    n = len(docs)
    df: Counter[str] = Counter()
    for toks in tokenized:
        for w in set(toks):
            df[w] += 1
    vectors = []
    for toks in tokenized:
        tf = Counter(toks)
        vec = {}
        for w, c in tf.items():
            idf = math.log((1 + n) / (1 + df[w])) + 1.0
            vec[w] = c * idf
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({w: v / norm for w, v in vec.items()})
    return vectors


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(w, 0.0) for w, v in a.items())


def recluster(conn: sqlite3.Connection) -> int:
    """Recompute clusters over the recent window. Returns number of clusters formed."""
    # Cluster ALL recent items (not just enriched) so duplicates collapse in the feed
    # even before summarization. Titles are the strongest cross-source signal, so weight
    # them heavily relative to the (outlet-specific) summary text.
    articles = db.recent_articles(conn, settings.cluster_window_days)
    if not articles:
        return 0

    docs = [f"{a.title} {a.title} {a.title} {a.summary or a.raw_summary}" for a in articles]
    vecs = _tfidf_vectors(docs)
    threshold = settings.cluster_threshold

    # Greedy clustering: each article joins the first cluster it's similar to.
    cluster_of: list[int] = [-1] * len(articles)
    centroids: list[dict[str, float]] = []
    members: list[list[int]] = []
    for i, vec in enumerate(vecs):
        best, best_sim = -1, threshold
        for ci, cen in enumerate(centroids):
            sim = _cosine(vec, cen)
            if sim >= best_sim:
                best, best_sim = ci, sim
        if best == -1:
            centroids.append(dict(vec))
            members.append([i])
            cluster_of[i] = len(centroids) - 1
        else:
            members[best].append(i)
            cluster_of[i] = best

    # Persist: reset clusters table, write new ones, assign article.cluster_id.
    conn.execute("DELETE FROM clusters")
    conn.execute("UPDATE articles SET cluster_id=NULL")
    formed = 0
    for ci, idxs in enumerate(members):
        if len(idxs) < 2:
            continue  # singletons stay unclustered
        # representative = highest importance, else most recent
        rep = max(idxs, key=lambda i: (articles[i].importance or 0))
        cur = conn.execute(
            "INSERT INTO clusters (label, top_article_id, size, created_at) VALUES (?,?,?,?)",
            (articles[rep].title, articles[rep].id, len(idxs), now_iso()),
        )
        cid = cur.lastrowid
        for i in idxs:
            db.set_cluster(conn, articles[i].id, cid)
        formed += 1
    conn.commit()
    log.info("clustering: %d multi-source clusters from %d articles", formed, len(articles))
    return formed
