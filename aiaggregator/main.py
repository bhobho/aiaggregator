"""FastAPI application: dashboard, scheduler, and lifecycle wiring."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import analytics, db
from .config import settings
from .enrich import cluster, summarize
from .ingest import pipeline
from .routes import admin, dashboard
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


# ----- visitor analytics (hidden) -------------------------------------------

def _client_ip(request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@app.middleware("http")
async def _track_visits(request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path
        skip = (request.method != "GET"
                or path.startswith("/static")
                or path in ("/feed", "/favicon.ico")
                or path == settings.analytics_path)
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
    return templates.TemplateResponse(request, "analytics.html", {"a": data})


app.add_api_route(settings.analytics_path, _analytics_view,
                  methods=["GET"], include_in_schema=False)
