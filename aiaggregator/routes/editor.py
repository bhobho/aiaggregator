"""Hidden blog editor: write posts locally (rich text + images), stored
straight in the `articles` table under a local "own blog" source so they show
up on My Page and render full-page exactly like the Medium/Hashnode posts do
(see queries.MY_SOURCES and routes.dashboard.post_view's `is_own` branch).

Not linked anywhere in the UI, and gated the same way as the analytics page
(settings.analytics_path/token in main.py): a secret ?key= from .env, 404 on
a wrong/missing key. Once the right key is seen, it's kept as a cookie so you
don't have to paste it into every link while writing.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import io
import re
import uuid

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse)
from PIL import Image, ImageOps

from .. import db, queries, sanitize
from ..config import settings
from ..models import Article, Source, now_iso

router = APIRouter()

EP = settings.editor_path
COOKIE_NAME = "aiagg_editor"

MAX_IMAGE_DIM = 1600
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB


# ----- auth (same obscurity + token pattern as the analytics page) ----------

def _is_authed(request: Request) -> bool:
    token = settings.editor_token
    if not token:
        return False  # no token configured -> hidden page is fully disabled
    return (request.query_params.get("key") == token
            or request.cookies.get(COOKIE_NAME) == token)


def _persist_auth_cookie(response, request: Request) -> None:
    token = settings.editor_token
    if token and request.query_params.get("key") == token:
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax",
                            max_age=60 * 60 * 24 * 30)


def _own_source_id(conn) -> int:
    """The local "own blog" source row, created on first use (idempotent)."""
    return db.upsert_source(conn, Source(
        name=queries.OWN_BLOG_SOURCE, url=queries.OWN_BLOG_SOURCE_URL,
        category="blog", active=True))


# ----- helpers ----------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _excerpt(clean_html: str, n: int = 220) -> str:
    """Plain-text teaser for feed cards, derived from the sanitized body (shown
    while the background enrichment pass hasn't produced a real summary yet)."""
    text = html_lib.unescape(_TAG_RE.sub(" ", clean_html or ""))
    text = " ".join(text.split())
    return text[:n]


async def _save_upload_image(file: UploadFile) -> str:
    raw = await file.read()
    if not raw:
        raise ValueError("Empty file")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Image too large (max 8MB)")
    try:
        probe = Image.open(io.BytesIO(raw))
        probe.verify()
    except Exception:
        raise ValueError("Not a valid image")

    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)  # respect orientation before EXIF is dropped
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > MAX_IMAGE_DIM:
        img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.LANCZOS)

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.jpg"
    # Re-saving from decoded pixels (rather than copying bytes) is what drops
    # EXIF/GPS metadata — no explicit stripping needed.
    img.save(settings.uploads_dir / fname, "JPEG", quality=85, optimize=True)
    return f"/uploads/{fname}"


# ----- routes ------------------------------------------------------------------

@router.get(EP, response_class=HTMLResponse)
async def editor_list(request: Request):
    if not _is_authed(request):
        return PlainTextResponse("Not Found", status_code=404)
    conn = db.connect()
    try:
        posts = queries.own_blog_posts(conn)
    finally:
        conn.close()
    templates = request.app.state.templates
    resp = templates.TemplateResponse(
        request, "editor_list.html",
        {"request": request, "posts": posts, "editor_path": EP})
    _persist_auth_cookie(resp, request)
    return resp


@router.get(f"{EP}/new", response_class=HTMLResponse)
async def editor_new(request: Request):
    if not _is_authed(request):
        return PlainTextResponse("Not Found", status_code=404)
    templates = request.app.state.templates
    resp = templates.TemplateResponse(
        request, "editor_form.html",
        {"request": request, "post": None, "editor_path": EP})
    _persist_auth_cookie(resp, request)
    return resp


@router.get(f"{EP}/edit/{{article_id}}", response_class=HTMLResponse)
async def editor_edit(request: Request, article_id: int):
    if not _is_authed(request):
        return PlainTextResponse("Not Found", status_code=404)
    conn = db.connect()
    try:
        row = db.get_article_row(conn, article_id)
        if row is None or row["source_id"] != _own_source_id(conn):
            return PlainTextResponse("Not Found", status_code=404)
        post = Article.from_row(row)
    finally:
        conn.close()
    templates = request.app.state.templates
    resp = templates.TemplateResponse(
        request, "editor_form.html",
        {"request": request, "post": post, "editor_path": EP})
    _persist_auth_cookie(resp, request)
    return resp


@router.post(f"{EP}/upload-image")
async def editor_upload_image(request: Request, file: UploadFile = File(...)):
    if not _is_authed(request):
        return PlainTextResponse("Not Found", status_code=404)
    try:
        url = await _save_upload_image(file)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"url": url})


@router.post(f"{EP}/save")
async def editor_save(request: Request, id: str = Form(""), title: str = Form(...),
                      content: str = Form(""), cover_image_url: str = Form("")):
    if not _is_authed(request):
        return PlainTextResponse("Not Found", status_code=404)
    title = title.strip()
    if not title:
        return PlainTextResponse("Title is required", status_code=400)

    clean_html = sanitize.clean(content)
    excerpt = _excerpt(clean_html)
    image_url = cover_image_url.strip() or None

    conn = db.connect()
    try:
        source_id = _own_source_id(conn)
        if id:
            article_id = int(id)
            row = db.get_article_row(conn, article_id)
            if row is None or row["source_id"] != source_id:
                return PlainTextResponse("Not Found", status_code=404)
            db.update_article(conn, article_id, title=title, content=clean_html,
                              raw_summary=excerpt, image_url=image_url)
        else:
            article = Article(
                source_id=source_id, guid=uuid.uuid4().hex, url="", title=title,
                content_hash=hashlib.sha256(clean_html.encode()).hexdigest(),
                author="Neeraj Pandey", published_at=now_iso(), fetched_at=now_iso(),
                raw_summary=excerpt, image_url=image_url, content=clean_html,
            )
            article_id = db.insert_article(conn, article)
            # Fill in the permalink now that the row (and its id) exists — for a
            # locally-written post this *is* the canonical URL (see the "View
            # original" link on post.html, which every post gets).
            conn.execute("UPDATE articles SET url=? WHERE id=?",
                        (f"/post/{article_id}", article_id))
            conn.commit()
    finally:
        conn.close()

    resp = RedirectResponse(url=EP, status_code=303)
    _persist_auth_cookie(resp, request)
    return resp


@router.post(f"{EP}/delete/{{article_id}}")
async def editor_delete(request: Request, article_id: int):
    if not _is_authed(request):
        return PlainTextResponse("Not Found", status_code=404)
    conn = db.connect()
    try:
        row = db.get_article_row(conn, article_id)
        if row is not None and row["source_id"] == _own_source_id(conn):
            db.delete_article(conn, article_id)
    finally:
        conn.close()
    resp = RedirectResponse(url=EP, status_code=303)
    _persist_auth_cookie(resp, request)
    return resp
