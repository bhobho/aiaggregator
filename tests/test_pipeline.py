import httpx
import pytest

from aiaggregator import db
from aiaggregator.ingest import pipeline
from tests.conftest import SAMPLE_RSS


@pytest.fixture
def mock_transport(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=SAMPLE_RSS, headers={"ETag": '"v1"'})

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


async def test_ingest_stores_and_dedups(conn, source_id, mock_transport):
    new1 = await pipeline.run_ingest(conn)
    assert new1 == 2  # both items new

    # second run: same content -> deduped, zero new
    new2 = await pipeline.run_ingest(conn)
    assert new2 == 0

    rows = conn.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"]
    assert rows == 2

    src = db.list_sources(conn)[0]
    assert src.last_status == "ok"
    assert src.etag == '"v1"'


async def test_pending_enrichment(conn, source_id, mock_transport):
    await pipeline.run_ingest(conn)
    pending = db.pending_enrichment(conn, limit=10)
    assert len(pending) == 2
    assert all(a.status == "new" for a in pending)
