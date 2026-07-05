import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Set by init_db once the pgvector extension is confirmed. Embedding writes and
# semantic search check this so the app still runs on a plain Postgres.
vector_enabled = False


async def get_session():
    async with SessionLocal() as session:
        yield session


# Additive migrations for tables that predate a column (create_all only creates tables).
MIGRATIONS = [
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS full_text TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS full_text_fetched_at TIMESTAMPTZ",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS summary TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS summary_short TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS summary_medium TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS summary_model VARCHAR(120)",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS summary_generated_at TIMESTAMPTZ",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS default_view VARCHAR(16) NOT NULL DEFAULT 'list'",
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS view_override VARCHAR(16)",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS entities_extracted_at TIMESTAMPTZ",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS tool_events JSONB",
    # Keyword leg of hybrid article search (the semantic leg is article_embeddings).
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS search_tsv tsvector GENERATED ALWAYS AS "
    "(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(excerpt, '') || ' ' "
    "|| coalesce(summary_medium, ''))) STORED",
    "CREATE INDEX IF NOT EXISTS ix_articles_search_tsv ON articles USING gin (search_tsv)",
]


async def init_db(max_attempts: int = 30) -> None:
    """Create tables, waiting for the database to accept connections."""
    global vector_enabled

    from . import models  # noqa: F401  (register mappings)

    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            break
        except Exception as exc:
            if attempt == max_attempts:
                raise
            logger.warning("Database not ready (attempt %d/%d): %s", attempt, max_attempts, exc)
            await asyncio.sleep(1)

    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        vector_enabled = True
    except Exception as exc:
        vector_enabled = False
        logger.warning("pgvector extension unavailable, semantic search disabled: %s", exc)

    async with engine.begin() as conn:
        tables = [
            table
            for table in Base.metadata.sorted_tables
            if vector_enabled or table.name != "article_embeddings"
        ]
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        for statement in MIGRATIONS:
            await conn.execute(text(statement))
