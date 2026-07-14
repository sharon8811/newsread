"""Shared test fixtures.

Runs against the real Postgres+pgvector on localhost:5433 (the docker-compose
`db` service) but a separate `newsread_test` database, created out-of-band. The
models use pgvector / JSONB / generated tsvector columns, so a real Postgres is
the faithful target; SQLite can't stand in.
"""

import os

# Point the app at the test database BEFORE anything imports app.config /
# app.db (the engine is built at import time from settings.database_url).
os.environ.setdefault(
    "NEWSREAD_DATABASE_URL",
    "postgresql+asyncpg://newsread:newsread@localhost:5433/newsread_test",
)
# Under pytest-xdist each worker runs in its own process and gets its own
# database (newsread_test_gw0, _gw1, ...), so parallel workers never see each
# other's rows and the per-test TRUNCATE stays safe. The databases are created
# on demand by the session-scoped _schema fixture below.
_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER")
if _XDIST_WORKER:
    _base, _, _dbname = os.environ["NEWSREAD_DATABASE_URL"].rpartition("/")
    os.environ["NEWSREAD_DATABASE_URL"] = f"{_base}/{_dbname}_{_XDIST_WORKER}"
# Neutralise anything the repo-root .env would otherwise inject, so tests are
# deterministic regardless of the developer's environment.
for _var in (
    "NEWSREAD_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "NEWSREAD_OPENAI_BASE_URL",
    "OPENAI_BASE_URL",
    "NEWSREAD_OPENAI_MODEL",
    "OPENAI_MODEL",
    "NEWSREAD_OPENAI_EMBEDDING_MODEL",
    "OPENAI_EMBEDDING_MODEL",
    "NEWSREAD_TAVILY_API_KEY",
    "TAVILY_API_KEY",
    "NEWSREAD_SEARXNG_BASE_URL",
    "SEARXNG_BASE_URL",
    "NEWSREAD_IMAGE_GENERATION_BASE_URL",
    "IMAGE_GENERATION_BASE_URL",
    "NEWSREAD_IMAGE_GENERATION_MODEL",
    "IMAGE_GENERATION_MODEL",
    "IMAGE_GENERTAION_MODEL",
    "NEWSREAD_IMAGE_GENERATION_API_KEY",
    "IMAGE_GENERATION_API_KEY",
    "NEWSREAD_IMAGE_GENERATION_EXTRA_PARAMS",
    "IMAGE_GENERATION_EXTRA_PARAMS",
    "NEWSREAD_GITHUB_TOKEN",
    "NEWSREAD_HF_TOKEN",
    "NEWSREAD_SLACK_CLIENT_ID",
    "NEWSREAD_SLACK_CLIENT_SECRET",
    "NEWSREAD_SLACK_SIGNING_SECRET",
    "NEWSREAD_TEAMS_CLIENT_ID",
    "NEWSREAD_TEAMS_CLIENT_SECRET",
):
    os.environ[_var] = ""

# Messaging-integration tests need deterministic values regardless of .env:
# a fixed (valid) Fernet key and known callback/frontend origins.
# bcrypt at production cost (12 rounds, ~0.2-0.3s per hash) dominates suite
# runtime — nearly every test creates users. 4 is bcrypt's minimum; the
# hash/verify round-trip stays fully exercised.
os.environ["NEWSREAD_BCRYPT_ROUNDS"] = "4"

os.environ["NEWSREAD_TOKEN_ENCRYPTION_KEY"] = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
os.environ["NEWSREAD_OAUTH_REDIRECT_BASE"] = "http://testserver"
os.environ["NEWSREAD_FRONTEND_BASE_URL"] = "http://front.test"
os.environ["NEWSREAD_TEAMS_TENANT"] = "organizations"

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import db as app_db
from app.config import settings

# The app engine uses pool_pre_ping=True, whose ping runs a sync await outside
# the async greenlet under tests (MissingGreenlet). Swap in an engine without
# pre-ping and rebind it everywhere the app reads it — including modules that
# imported SessionLocal by value (worker, pipeline). Pooling is safe here (and
# much faster than NullPool's connection-per-session) because pytest.ini pins
# one session-scoped event loop, so pooled connections never cross loops.
engine = create_async_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
app_db.engine = engine
app_db.SessionLocal = SessionLocal

import app.enrichers.pipeline as _pipeline  # noqa: E402
import app.worker as _worker  # noqa: E402

_worker.SessionLocal = SessionLocal
_pipeline.SessionLocal = SessionLocal

from app.db import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import create_access_token, hash_password  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
    """Create the (per-xdist-worker) database and schema once per session."""
    from app import models  # noqa: F401  register mappings

    if _XDIST_WORKER:
        # CREATE DATABASE can't run inside a transaction, hence AUTOCOMMIT.
        base, _, dbname = settings.database_url.rpartition("/")
        admin = create_async_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": dbname},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
        await admin.dispose()

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    app_db.vector_enabled = True
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in app_db.MIGRATIONS:
            await conn.execute(text(statement))
    yield
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean():
    """Truncate every table before each test for full isolation."""
    async with engine.begin() as conn:
        tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    from app import embeddings

    embeddings._query_cache.clear()
    yield


@pytest.fixture(autouse=True)
def _skip_feed_url_validation(monkeypatch):
    """Feed fetches use respx-mocked hosts that must never hit real DNS.
    Tests exercising the guard call the imported _validate_public_url directly,
    which keeps its original binding."""

    async def allow(url: str) -> None:
        return None

    monkeypatch.setattr("app.fetcher._validate_public_url", allow)


@pytest_asyncio.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class UserFactory:
    """Create users directly in the DB and mint auth headers for them."""

    def __init__(self, session):
        self.session = session
        self._n = 0

    async def create(
        self,
        *,
        username=None,
        email=None,
        name="Test User",
        password="password123",
        default_view="list",
    ) -> User:
        self._n += 1
        username = username or f"user{self._n}"
        email = email or f"{username}@example.com"
        user = User(
            email=email.lower(),
            username=username,
            name=name,
            password_hash=hash_password(password),
            default_view=default_view,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    def auth(self, user: User) -> dict:
        return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest_asyncio.fixture
async def users(session):
    return UserFactory(session)


class DataFactory:
    """Create feeds, subscriptions, and articles for router tests."""

    def __init__(self, session):
        self.session = session
        self._n = 0

    async def feed(self, *, url=None, title="A Feed", **kwargs):
        from app.models import Feed

        self._n += 1
        feed = Feed(url=url or f"https://feed{self._n}.example/rss", title=title, **kwargs)
        self.session.add(feed)
        await self.session.commit()
        await self.session.refresh(feed)
        return feed

    async def subscribe(self, user, feed, view_override=None, **kwargs):
        from app.models import Subscription

        sub = Subscription(user_id=user.id, feed_id=feed.id, view_override=view_override, **kwargs)
        self.session.add(sub)
        await self.session.commit()
        await self.session.refresh(sub)
        return sub

    async def article(self, feed, *, guid=None, title="An Article", **kwargs):
        from app.models import Article

        self._n += 1
        guid = guid or f"guid-{self._n}"
        defaults = dict(
            url=f"https://site.example/{guid}",
            title=title,
            excerpt="an excerpt",
            content_html="<p>body</p>",
        )
        defaults.update(kwargs)
        article = Article(feed_id=feed.id, guid=guid, **defaults)
        self.session.add(article)
        await self.session.commit()
        await self.session.refresh(article)
        return article

    async def state(self, user, article, *, is_read=False, is_saved=False):
        from app.models import UserArticleState

        st = UserArticleState(
            user_id=user.id, article_id=article.id, is_read=is_read, is_saved=is_saved
        )
        self.session.add(st)
        await self.session.commit()
        return st


@pytest_asyncio.fixture
async def data(session):
    return DataFactory(session)


@pytest_asyncio.fixture(autouse=True)
def _no_enqueue(monkeypatch):
    """Feed/share routes enqueue background jobs; keep tests off Redis."""

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.routers.feeds.enqueue", _noop)
    monkeypatch.setattr("app.routers.shares.enqueue", _noop)
    monkeypatch.setattr("app.routers.projects.enqueue", _noop)
