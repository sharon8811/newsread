import asyncio
import logging
from pathlib import Path

from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from alembic import command as alembic_command

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


# Schema lives in Alembic revisions (backend/alembic/). init_db upgrades to
# head on every boot; pre-Alembic databases (built by the old create_all +
# MIGRATIONS path) are stamped at baseline on first contact.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Serializes schema setup across concurrently booting processes (the API and
# the arq worker start together in docker-compose). Arbitrary but stable.
_SCHEMA_LOCK_KEY = 0x6E657773


def alembic_config() -> AlembicConfig:
    return AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))


# Data repairs and backfills. Each named group runs exactly once per
# database (tracked in one_shot_migrations) and must stay idempotent anyway:
# groups that predate the Alembic switch already ran on old databases via the
# retired MIGRATIONS list, then were re-claimed (as no-ops) under this
# mechanism.
ONE_SHOT_MIGRATIONS: dict[str, list[str]] = {
    # Recover HN thread references for rows ingested before content-based
    # discussion detection. Strict host/path matching avoids generic HN links;
    # anchoring on the hnrss 'Comments URL:' label skips other HN threads a
    # self-post's body may link before it.
    "backfill_hn_comments_url": [
        "UPDATE articles SET comments_url = 'https://news.ycombinator.com/item?id=' || "
        "substring(url from 'news\\.ycombinator\\.com/item\\?id=([0-9]+)') "
        "WHERE comments_url IS NULL AND url ~* '^https?://news\\.ycombinator\\.com/item\\?id=[0-9]+'",
        "UPDATE articles SET comments_url = 'https://news.ycombinator.com/item?id=' || "
        "substring(substring(content_html from '(?i)Comments URL:.*') "
        "from 'news\\.ycombinator\\.com/item\\?id=([0-9]+)') "
        "WHERE comments_url IS NULL "
        "AND content_html ~* 'Comments URL:.*news\\.ycombinator\\.com/item\\?id=[0-9]+'",
    ],
    # The zen view was replaced by the cards view; remap stored preferences.
    "remap_zen_view_to_cards": [
        "UPDATE users SET default_view = 'cards' WHERE default_view = 'zen'",
        "UPDATE subscriptions SET view_override = 'cards' WHERE view_override = 'zen'",
    ],
    # Ticket threads: legacy per-pin notes become each thread's first comment
    # (author = the pin's adder, timestamp = pin time), then the notes are
    # cleared. The pair is idempotent because the second statement empties
    # what the first selects; both run in init_db's single transaction.
    "migrate_pin_notes_to_comments": [
        "INSERT INTO project_article_comments (project_id, article_id, author_id, body, created_at) "
        "SELECT project_id, article_id, added_by_user_id, note, created_at FROM project_articles "
        "WHERE note IS NOT NULL AND btrim(note) <> ''",
        "UPDATE project_articles SET note = NULL WHERE note IS NOT NULL",
    ],
    # Generated-image URLs were briefly stored absolute (built from the OAuth
    # redirect base, which may point at a tunnel browsers can't reach);
    # relative paths survive any deployment host. Idempotent: already-relative
    # rows are excluded by the NOT LIKE.
    "relativize_generated_image_urls": [
        "UPDATE articles SET image_url = '/api/articles/' || id || '/generated-image' "
        "WHERE image_url LIKE '%/api/articles/%/generated-image' AND image_url NOT LIKE '/api/%'",
    ],
}


async def _clean_hnrss_content(conn) -> None:
    """Use the ingestion cleaner to repair HNRSS rows already in the DB."""
    from .fetcher import derive_excerpt, strip_hnrss_boilerplate

    rows = (
        await conn.execute(
            text(
                "SELECT id, content_html, comments_url FROM articles "
                "WHERE comments_url ~* '^https?://news\\.ycombinator\\.com/item\\?id=[0-9]+' "
                "AND content_html ILIKE '%Comments URL:%'"
            )
        )
    ).mappings()
    for row in rows:
        cleaned = strip_hnrss_boilerplate(row["content_html"], row["comments_url"])
        if cleaned == row["content_html"]:
            continue
        await conn.execute(
            text(
                "UPDATE articles SET content_html = :content_html, excerpt = :excerpt "
                "WHERE id = :id"
            ),
            {
                "id": row["id"],
                "content_html": cleaned,
                "excerpt": derive_excerpt(cleaned),
            },
        )


ONE_SHOT_REPAIRS = {"clean_hnrss_boilerplate_lxml": _clean_hnrss_content}


async def init_db(max_attempts: int = 30) -> None:
    """Migrate the schema to head, waiting for the database to accept connections."""
    global vector_enabled

    from . import models  # noqa: F401  (register mappings)
    from .seeds import seed_catalog

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
    except Exception as exc:
        raise RuntimeError(
            "The pgvector extension is required (the compose stack ships "
            "pgvector/pgvector:pg16) but could not be created"
        ) from exc
    vector_enabled = True

    async with engine.begin() as conn:
        # Both the API and the arq worker run init_db at boot; the lock makes
        # the loser wait instead of racing the DDL. Transaction-scoped, so it
        # releases even on failure.
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SCHEMA_LOCK_KEY})
        stamped = await conn.scalar(text("SELECT to_regclass('public.alembic_version')"))
        populated = await conn.scalar(text("SELECT to_regclass('public.users')"))

        def _migrate(sync_conn) -> None:
            config = alembic_config()
            config.attributes["connection"] = sync_conn
            if stamped is None and populated is not None:
                # Pre-Alembic database built by the retired create_all +
                # MIGRATIONS path; its schema equals the baseline revision.
                alembic_command.stamp(config, "head")
            else:
                alembic_command.upgrade(config, "head")

        await conn.run_sync(_migrate)
        # Redundant on fresh databases (the baseline creates it); pre-Alembic
        # databases got it from the old init path, so this is belt-and-braces.
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS one_shot_migrations "
                "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
        )
        for name, statements in ONE_SHOT_MIGRATIONS.items():
            claimed = await conn.execute(
                text(
                    "INSERT INTO one_shot_migrations (name) VALUES (:name) "
                    "ON CONFLICT (name) DO NOTHING"
                ),
                {"name": name},
            )
            if claimed.rowcount:
                for statement in statements:
                    await conn.execute(text(statement))
        for name, repair in ONE_SHOT_REPAIRS.items():
            claimed = await conn.execute(
                text(
                    "INSERT INTO one_shot_migrations (name) VALUES (:name) "
                    "ON CONFLICT (name) DO NOTHING"
                ),
                {"name": name},
            )
            if claimed.rowcount:
                await repair(conn)
        await seed_catalog(conn)
