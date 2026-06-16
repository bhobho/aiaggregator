"""FastAPI application: dashboard, scheduler, and lifecycle wiring."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .config import settings
from .enrich import cluster, digest, summarize
from .ingest import pipeline
from .markdown import md_to_html
from .routes import admin, dashboard
from .timefmt import timeago

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aiaggregator")

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.filters["markdown"] = md_to_html
templates.env.filters["timeago"] = timeago


async def _job_fetch() -> None:
    conn = db.connect()
    try:
        n = await pipeline.run_ingest(conn)
        log.info("scheduled fetch: %d new", n)
    finally:
        conn.close()


async def _job_enrich() -> None:
    conn = db.connect()
    try:
        n = await summarize.run_enrichment(conn, settings.enrich_batch)
        if n:
            cluster.recluster(conn)
    finally:
        conn.close()


async def _job_digest() -> None:
    conn = db.connect()
    try:
        cluster.recluster(conn)
        digest.save_digest(conn)
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
    scheduler.add_job(_job_digest, "cron", hour=settings.digest_hour, id="digest")
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
app.state.jobs = {"fetch": _job_fetch, "enrich": _job_enrich, "digest": _job_digest}

app.include_router(dashboard.router)
app.include_router(admin.router)
