"""FastAPI application: dashboard, scheduler, and lifecycle wiring."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import json
from urllib.parse import urlparse

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import analytics, db
from .config import settings
from .enrich import cluster, summarize
from .ingest import pipeline
from .routes import admin, dashboard, seo
from .timefmt import timeago

logging.basicConfig(level=logging.ERROR,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aiaggregator")

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.filters["timeago"] = timeago
templates.env.globals["public_url"] = settings.public_url.rstrip("/")


async def _job_fetch() -> None:
    conn = db.connect()
    try:
        n = await pipeline.run_ingest(conn)
        await cluster.recluster(conn)  # collapse cross-source duplicates right away
        log.info("scheduled fetch: %d new", n)
    finally:
        conn.close()


async def _job_enrich() -> None:
    conn = db.connect()
    try:
        n = await summarize.run_enrichment(conn, settings.enrich_batch)
        if n:
            await cluster.recluster(conn)
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.init_db(conn)
    pipeline.sync_sources(conn)
    conn.close()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(_job_fetch, "interval", seconds=settings.fetch_interval,
                      id="fetch", next_run_time=None)
    scheduler.add_job(_job_enrich, "interval", seconds=settings.enrich_interval, id="enrich")
    scheduler.start()
    app.state.scheduler = scheduler

    # Kick off an initial fetch shortly after startup (non-blocking).
    import asyncio
    asyncio.create_task(_job_fetch())

    log.info("aiaggregator started")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="aiaggregator", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

# share templates + job callables with routers
app.state.templates = templates
app.state.jobs = {"fetch": _job_fetch, "enrich": _job_enrich}

app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(seo.router)


# ----- visitor analytics (hidden) -------------------------------------------

def _client_ip(request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _is_excluded(ip: str | None) -> bool:
    """True for the site owner's own IP(s) — see AIAGG_ANALYTICS_EXCLUDE_IPS."""
    return bool(ip) and ip in settings.analytics_exclude_ip_set


@app.middleware("http")
async def _track_visits(request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path
        skip = (request.method != "GET"
                or path.startswith("/static")
                or path.startswith("/beacon")
                or path in ("/feed", "/favicon.ico")
                or path == settings.analytics_path
                or _is_excluded(_client_ip(request)))
        if not skip:
            conn = db.connect()
            try:
                db.record_visit(
                    conn,
                    ip=_client_ip(request),
                    path=path,
                    method=request.method,
                    referer=request.headers.get("referer"),
                    user_agent=request.headers.get("user-agent"),
                )
            finally:
                conn.close()
    except Exception as exc:  # never let tracking break a page
        log.warning("visit tracking failed: %s", exc)
    return response


async def _read_beacon_json(request: Request) -> dict:
    """navigator.sendBeacon posts a Blob (often text/plain), so parse raw bytes
    rather than relying on FastAPI's content-type-based JSON parsing."""
    try:
        return json.loads((await request.body()) or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


@app.post("/beacon/dwell", include_in_schema=False)
async def beacon_dwell(request: Request) -> Response:
    """Time-on-page: the client reports elapsed ms when the tab hides/closes."""
    ip = _client_ip(request)
    if _is_excluded(ip):
        return Response(status_code=204)
    data = await _read_beacon_json(request)
    path = str(data.get("path") or "")[:200]
    try:
        ms = int(data.get("ms") or 0)
    except (TypeError, ValueError):
        ms = 0
    if path.startswith("/") and 250 <= ms <= 3 * 60 * 60 * 1000:  # sane bounds
        conn = db.connect()
        try:
            db.record_engagement(conn, ip=ip, path=path, duration_ms=ms)
        finally:
            conn.close()
    return Response(status_code=204)


@app.post("/beacon/outbound", include_in_schema=False)
async def beacon_outbound(request: Request) -> Response:
    """Logged when a visitor clicks through to a story's original source."""
    ip = _client_ip(request)
    if _is_excluded(ip):
        return Response(status_code=204)
    data = await _read_beacon_json(request)
    path = str(data.get("path") or "")[:200]
    url = str(data.get("url") or "")
    domain = urlparse(url).netloc.removeprefix("www.").lower()
    if domain and path.startswith("/"):
        conn = db.connect()
        try:
            db.record_outbound_click(conn, ip=ip, path=path, domain=domain, dest_url=url[:500])
        finally:
            conn.close()
    return Response(status_code=204)


async def _analytics_view(request: Request):
    # Token gate: if configured, a wrong/missing key returns 404 (hides existence).
    if settings.analytics_token and request.query_params.get("key") != settings.analytics_token:
        return PlainTextResponse("Not Found", status_code=404)
    conn = db.connect()
    try:
        await analytics.resolve_pending(conn)
        data = analytics.summary(conn, days=30)
    finally:
        conn.close()
    my_ip = _client_ip(request)
    return templates.TemplateResponse(
        request, "analytics.html",
        {"a": data, "my_ip": my_ip, "my_ip_excluded": _is_excluded(my_ip),
         "robots_meta": "noindex, nofollow"})


app.add_api_route(settings.analytics_path, _analytics_view,
                  methods=["GET"], include_in_schema=False)
