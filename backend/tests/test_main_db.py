import asyncio

import pytest
from sqlalchemy import text

from app import db as app_db
from app.db import get_session, init_db
from app.main import app, health, lifespan


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_health_function_directly():
    assert await health() == {"status": "ok"}


async def test_get_session_yields_session():
    gen = get_session()
    session = await gen.__anext__()
    result = await session.execute(text("SELECT 1"))
    assert result.scalar() == 1
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


async def test_init_db_happy_path():
    # Schema already exists (created by the session fixture); init_db is idempotent.
    await init_db()
    assert app_db.vector_enabled is True


async def test_init_db_retries_then_raises(monkeypatch):
    monkeypatch.setattr(app_db.asyncio, "sleep", _instant_sleep)

    class BrokenConn:
        async def __aenter__(self):
            raise ConnectionError("db not ready")

        async def __aexit__(self, *a):
            return False

    class FakeEngine:
        def connect(self):
            return BrokenConn()

    monkeypatch.setattr(app_db, "engine", FakeEngine())
    with pytest.raises(ConnectionError):
        await init_db(max_attempts=2)


async def test_lifespan_runs_init(monkeypatch):
    called = {}

    async def fake_init():
        called["init"] = True

    monkeypatch.setattr("app.main.init_db", fake_init)
    async with lifespan(app):
        pass
    assert called["init"]


async def _instant_sleep(seconds):
    return None
