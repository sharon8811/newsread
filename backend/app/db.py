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
    # Per-subscription feed settings + the global per-feed AI switch.
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS title_override VARCHAR(512)",
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS sort_order VARCHAR(16)",
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS retention_days INTEGER",
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_muted BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE feeds ADD COLUMN IF NOT EXISTS ai_enabled BOOLEAN NOT NULL DEFAULT TRUE",
    # Keyword leg of hybrid article search (the semantic leg is article_embeddings).
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS search_tsv tsvector GENERATED ALWAYS AS "
    "(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(excerpt, '') || ' ' "
    "|| coalesce(summary_medium, ''))) STORED",
    "CREATE INDEX IF NOT EXISTS ix_articles_search_tsv ON articles USING gin (search_tsv)",
    # Per-member project push mute (project_members predates the column).
    "ALTER TABLE project_members ADD COLUMN IF NOT EXISTS is_muted BOOLEAN NOT NULL DEFAULT FALSE",
    # Project-wide Q&A threads: conversations gain an optional project scope.
    "ALTER TABLE conversations ALTER COLUMN article_id DROP NOT NULL",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS project_id INTEGER "
    "REFERENCES projects(id) ON DELETE CASCADE",
    "CREATE INDEX IF NOT EXISTS ix_conversations_project_id ON conversations (project_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_project_user "
    "ON conversations (project_id, user_id) WHERE project_id IS NOT NULL",
    # The zen view was replaced by the cards view; remap stored preferences.
    "ALTER TABLE users ALTER COLUMN default_view SET DEFAULT 'cards'",
    "UPDATE users SET default_view = 'cards' WHERE default_view = 'zen'",
    "UPDATE subscriptions SET view_override = 'cards' WHERE view_override = 'zen'",
    # Ticket threads: legacy per-pin notes become each thread's first comment
    # (author = the pin's adder, timestamp = pin time), then the notes are
    # cleared. The pair is idempotent because the second statement empties
    # what the first selects; both run in init_db's single transaction.
    "INSERT INTO project_article_comments (project_id, article_id, author_id, body, created_at) "
    "SELECT project_id, article_id, added_by_user_id, note, created_at FROM project_articles "
    "WHERE note IS NOT NULL AND btrim(note) <> ''",
    "UPDATE project_articles SET note = NULL WHERE note IS NOT NULL",
    # Generated article images (bring-your-own-key feature).
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS image_prompt TEXT",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS image_gen_attempted_at TIMESTAMPTZ",
    # Generated-image URLs were briefly stored absolute (built from the OAuth
    # redirect base, which may point at a tunnel browsers can't reach);
    # relative paths survive any deployment host. Idempotent: already-relative
    # rows are excluded by the NOT LIKE.
    "UPDATE articles SET image_url = '/api/articles/' || id || '/generated-image' "
    "WHERE image_url LIKE '%/api/articles/%/generated-image' AND image_url NOT LIKE '/api/%'",
    # Screenshot summaries: whether the user's own model accepts image input.
    "ALTER TABLE user_ai_settings ADD COLUMN IF NOT EXISTS supports_vision "
    "BOOLEAN NOT NULL DEFAULT FALSE",
    # Per-feed image-generation switch + per-user monthly budget; the article
    # column attributes each generation claim to the user whose budget it spends.
    "ALTER TABLE feeds ADD COLUMN IF NOT EXISTS image_gen_enabled BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS image_gen_monthly_limit INTEGER",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS image_gen_user_id INTEGER "
    "REFERENCES users(id) ON DELETE SET NULL",
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
