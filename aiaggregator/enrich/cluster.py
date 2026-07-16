"""Group duplicate stories across sources so each story is shown once.

Primary path: semantic title embeddings from the local Ollama embed model
(nomic-embed-text), which recognize paraphrased headlines of the same story
("Anthropic Makes Claude Free for K-12 Teachers" vs "Anthropic launches free
Claude for Teachers"). Embeddings are cached per article in the DB, so each
title is embedded once.

Fallback: the original pure-Python TF-IDF cosine over titles+summaries, used
when the embed model is unavailable.
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
from collections import Counter

import numpy as np

from .. import db
from ..config import settings
from ..models import Article, now_iso
from ..textnorm import strip_outlet_suffix
from . import ollama_client

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "is",
    "are", "as", "at", "by", "from", "that", "this", "it", "its", "be", "new",
    "ai", "model", "models",
}


# ----- embedding path ---------------------------------------------------------

async def _embed_members(conn: sqlite3.Connection,
                         articles: list[Article]) -> list[list[int]] | None:
    """Cluster by cached title embeddings. Returns member index groups, or
    None if the embed model is unavailable (caller falls back to TF-IDF)."""
    models = await ollama_client.list_models()
    base = settings.ollama_embed_model.split(":")[0]
    if not any(m.split(":")[0] == base for m in models):
        return None

    ids = [a.id for a in articles]
    stored = db.embeddings_map(conn, ids)
    fresh: list[tuple[int, bytes]] = []
    for a in articles:
        if a.id in stored:
            continue
        vec = await ollama_client.embed(strip_outlet_suffix(a.title))
        if vec is None:
            log.warning("embedding failed mid-recluster; falling back to TF-IDF")
            return None
        blob = np.asarray(vec, dtype=np.float32).tobytes()
        stored[a.id] = blob
        fresh.append((a.id, blob))
    if fresh:
        db.save_embeddings(conn, fresh)

    # Drop stale vectors with a different dimension (e.g. embed model changed).
    dims = Counter(len(b) for b in stored.values())
    dim = dims.most_common(1)[0][0]
    usable = [i for i, a in enumerate(articles) if len(stored[a.id]) == dim]

    mat = np.empty((len(usable), dim // 4), dtype=np.float32)
    for row, i in enumerate(usable):
        mat[row] = np.frombuffer(stored[articles[i].id], dtype=np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9

    # Greedy leader clustering: each story joins the most similar leader.
    threshold = settings.embed_cluster_threshold
    leaders = np.empty_like(mat)
    n_leaders = 0
    members: list[list[int]] = []
    for row, i in enumerate(usable):
        if n_leaders:
            sims = leaders[:n_leaders] @ mat[row]
            j = int(np.argmax(sims))
            if sims[j] >= threshold:
                members[j].append(i)
                continue
        leaders[n_leaders] = mat[row]
        n_leaders += 1
        members.append([i])
    return members


# ----- TF-IDF fallback --------------------------------------------------------

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


def _tfidf_members(articles: list[Article]) -> list[list[int]]:
    # Titles are the strongest cross-source signal, so weight them heavily
    # relative to the (outlet-specific) summary text.
    docs = []
    for a in articles:
        t = strip_outlet_suffix(a.title)
        docs.append(f"{t} {t} {t} {a.summary or a.raw_summary}")
    vecs = _tfidf_vectors(docs)
    threshold = settings.cluster_threshold

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
    return members


# ----- shared persistence -----------------------------------------------------

def _persist(conn: sqlite3.Connection, articles: list[Article],
             members: list[list[int]]) -> int:
    """Swap in the new cluster assignments in ONE transaction. Reclustering
    runs every minute; a per-row commit would let concurrent page renders see
    half-cleared cluster ids and show duplicate stories."""
    conn.execute("DELETE FROM clusters")
    conn.execute("UPDATE articles SET cluster_id=NULL")
    formed = 0
    for idxs in members:
        if len(idxs) < 2:
            continue  # singletons stay unclustered
        # representative = highest importance, else most recent
        rep = max(idxs, key=lambda i: (articles[i].importance or 0))
        cur = conn.execute(
            "INSERT INTO clusters (label, top_article_id, size, created_at) VALUES (?,?,?,?)",
            (articles[rep].title, articles[rep].id, len(idxs), now_iso()),
        )
        conn.executemany(
            "UPDATE articles SET cluster_id=? WHERE id=?",
            [(cur.lastrowid, articles[i].id) for i in idxs],
        )
        formed += 1
    conn.commit()
    return formed


async def recluster(conn: sqlite3.Connection) -> int:
    """Recompute clusters over the recent window. Returns number of clusters formed."""
    # Cluster ALL recent items (not just enriched) so duplicates collapse in the
    # feed even before summarization.
    articles = db.recent_articles(conn, settings.cluster_window_days)
    if not articles:
        return 0

    members = await _embed_members(conn, articles)
    method = "embeddings"
    if members is None:
        members = _tfidf_members(articles)
        method = "tf-idf"

    formed = _persist(conn, articles, members)
    log.info("clustering (%s): %d multi-source clusters from %d articles",
             method, formed, len(articles))
    return formed
